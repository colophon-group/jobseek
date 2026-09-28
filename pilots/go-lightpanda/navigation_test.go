package main

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func TestNavigationWaitStatesAndLoaderReset(t *testing.T) {
	s := newNavigationState("main")
	now := time.Now()
	for _, wait := range []runtimev1.WaitCondition{1, 2, 3, 4} {
		if ready, _ := s.readiness(wait, now.Add(time.Second)); ready {
			t.Fatal("uncommitted document admitted")
		}
	}
	s.observe(&page.EventFrameNavigated{Frame: &cdp.Frame{ID: "other", LoaderID: "subframe", URL: "https://fixture.test/frame"}}, now)
	if ready, _ := s.readiness(1, now); ready {
		t.Fatal("subframe committed main document")
	}
	s.commit("loader", now)
	if ready, _ := s.readiness(1, now); !ready {
		t.Fatal("commit was not ready")
	}
	if ready, _ := s.readiness(2, now); ready {
		t.Fatal("DOM ready before event")
	}
	s.observe(&page.EventDomContentEventFired{}, now)
	if ready, _ := s.readiness(2, now); !ready {
		t.Fatal("DOM event ignored")
	}
	if ready, _ := s.readiness(3, now); ready {
		t.Fatal("load ready before event")
	}
	s.observe(&page.EventLoadEventFired{}, now)
	if ready, _ := s.readiness(3, now); !ready {
		t.Fatal("load event ignored")
	}
	s.commit("new-loader", now.Add(time.Second))
	if ready, _ := s.readiness(3, now.Add(time.Second)); ready {
		t.Fatal("previous loader load state leaked")
	}
	if ready, delay := s.readiness(4, now.Add(time.Second)); ready || delay != networkIdlePeriod {
		t.Fatal("previous loader idle state leaked")
	}
}

func TestNavigationNetworkIdleCountsRedirectsFailuresAndSubresources(t *testing.T) {
	s := newNavigationState("main")
	now := time.Now()
	s.commit("loader", now)
	start := &network.EventRequestWillBeSent{RequestID: "redirect", Type: network.ResourceTypeDocument}
	s.observe(start, now)
	s.observe(start, now.Add(time.Millisecond))
	s.observe(&network.EventRequestWillBeSent{RequestID: "subresource", Type: network.ResourceTypeFetch}, now)
	s.observe(&network.EventRequestWillBeSent{RequestID: "socket", Type: network.ResourceTypeWebSocket}, now)
	s.observe(&network.EventLoadingFinished{RequestID: "redirect"}, now)
	if ready, _ := s.readiness(4, now.Add(time.Second)); ready {
		t.Fatal("pending subresource did not block idle")
	}
	s.observe(&network.EventLoadingFailed{RequestID: "subresource"}, now)
	if ready, _ := s.readiness(4, now.Add(networkIdlePeriod-time.Nanosecond)); ready {
		t.Fatal("idle interval was shortened")
	}
	if ready, _ := s.readiness(4, now.Add(networkIdlePeriod)); !ready {
		t.Fatal("finished redirect or websocket remained pending")
	}
	s.observe(&network.EventRequestWillBeSent{RequestID: "later", Type: network.ResourceTypeFetch}, now.Add(time.Second))
	if ready, _ := s.readiness(4, now.Add(2*time.Second)); ready {
		t.Fatal("later resource did not reset idle")
	}
}

func TestNavigationTimeoutFallbackDoesNotNavigateAgain(t *testing.T) {
	s := newNavigationState("main")
	calls := 0
	err := navigateDocument(context.Background(), s, navigationOptions{wait: 4, timeout: 20 * time.Millisecond, fallback: &navigationOptions{wait: 2, timeout: 20 * time.Millisecond}}, func(ctx context.Context) error {
		calls++
		s.commit("loader", time.Now())
		s.observe(&page.EventDomContentEventFired{}, time.Now())
		s.observe(&network.EventRequestWillBeSent{RequestID: "held", Type: network.ResourceTypeFetch}, time.Now())
		return nil
	})
	if err != nil || calls != 1 {
		t.Fatalf("fallback result=%v navigations=%d", err, calls)
	}
}

func TestNavigationFallbackWhenNavigateCommandTimesOut(t *testing.T) {
	s := newNavigationState("main")
	calls := 0
	err := navigateDocument(context.Background(), s, navigationOptions{wait: 3, timeout: 20 * time.Millisecond, fallback: &navigationOptions{wait: 2, timeout: 20 * time.Millisecond}}, func(ctx context.Context) error {
		calls++
		s.commit("loader", time.Now())
		s.observe(&page.EventDomContentEventFired{}, time.Now())
		<-ctx.Done()
		return ctx.Err()
	})
	if err != nil || calls != 1 {
		t.Fatalf("committed document was lost: %v/%d", err, calls)
	}
}

func TestNavigationErrorCancellationAndMissingCommitFail(t *testing.T) {
	for _, kind := range []string{"origin-error", "cancelled", "uncommitted", "disabled"} {
		t.Run(kind, func(t *testing.T) {
			s := newNavigationState("main")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			calls := 0
			options := navigationOptions{wait: 4, timeout: 10 * time.Millisecond, fallback: &navigationOptions{wait: 2, timeout: 10 * time.Millisecond}}
			if kind == "disabled" {
				options.fallback = nil
			}
			err := navigateDocument(ctx, s, options, func(ctx context.Context) error {
				calls++
				if kind == "origin-error" {
					return errors.New("navigation failed")
				}
				if kind == "cancelled" {
					cancel()
					return context.Canceled
				}
				return nil
			})
			if err == nil || calls != 1 {
				t.Fatalf("failure became success/duplicate navigation: %v/%d", err, calls)
			}
			if kind == "cancelled" && !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation was replaced by fallback")
			}
		})
	}
}

func TestNavigationTransportRetryIsConditionalSamePageAndBounded(t *testing.T) {
	for _, failure := range []string{"RecvError", "SendError", "net::ERR_CONNECTION_RESET", "CouldntConnect", "ResolveHost", "timeout", "cancelled", "disabled", "twice"} {
		t.Run(failure, func(t *testing.T) {
			state := newNavigationState("main")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			options := navigationOptions{wait: 1, timeout: 20 * time.Millisecond, transportRetries: 1}
			if failure == "disabled" {
				options.transportRetries = 0
			}
			calls := 0
			start := time.Now()
			err := navigateDocument(ctx, state, options, func(call context.Context) error {
				calls++
				if calls == 2 && failure != "twice" {
					state.commit("recovered", time.Now())
					return nil
				}
				if failure == "timeout" {
					<-call.Done()
					return call.Err()
				}
				if failure == "cancelled" {
					cancel()
					return ctx.Err()
				}
				if failure == "disabled" || failure == "twice" {
					return navigationError("RecvError")
				}
				return navigationError(failure)
			})
			wantRetry := failure == "RecvError" || failure == "SendError" || failure == "net::ERR_CONNECTION_RESET" || failure == "twice"
			if wantRetry && (calls != 2 || time.Since(start) < 500*time.Millisecond) {
				t.Fatalf("transport retry count/delay changed: %d %v", calls, err)
			}
			if !wantRetry && calls != 1 {
				t.Fatal("non-retryable navigation repeated")
			}
			if wantRetry && failure != "twice" && err != nil {
				t.Fatal(err)
			}
			if (!wantRetry || failure == "twice") && err == nil {
				t.Fatal("failed navigation admitted")
			}
		})
	}
}

func TestNavigationFailureCorrelatesOnlyMainDocumentAndRejectsNativePseudoPage(t *testing.T) {
	state := newNavigationState("main")
	now := time.Now()
	state.observe(&network.EventRequestWillBeSent{RequestID: "document", FrameID: "main", Type: network.ResourceTypeDocument}, now)
	state.commit("loader", now)
	state.observe(&page.EventLoadEventFired{}, now)
	state.observe(&network.EventLoadingFailed{RequestID: "subresource", ErrorText: "RecvError"}, now)
	if state.documentFailure != nil {
		t.Fatal("subresource failure became document retry")
	}
	if ready, _ := state.readiness(3, now); ready {
		t.Fatal("status-free pseudo-document became ready")
	}
	state.observe(&network.EventResponseReceived{RequestID: "document", FrameID: "main", Type: network.ResourceTypeDocument, Response: &network.Response{Status: 0}}, now)
	var transport *navigationTransportError
	if !errors.As(state.documentFailure, &transport) || !transport.retryable {
		t.Fatal("native pre-header receive failure not classified")
	}
	state.observe(&network.EventRequestWillBeSent{RequestID: "second", FrameID: "main", Type: network.ResourceTypeDocument}, now)
	state.observe(&network.EventResponseReceived{RequestID: "second", FrameID: "main", Type: network.ResourceTypeDocument, Response: &network.Response{Status: 403}}, now)
	if state.documentFailure != nil || !state.documentStatusValid {
		t.Fatal("HTTP status became transport retry")
	}
	state.observe(&network.EventLoadingFailed{RequestID: "second", Type: network.ResourceTypePing, ErrorText: "RecvError"}, now)
	if !errors.As(state.documentFailure, &transport) || !transport.retryable {
		t.Fatal("native Ping type broke document identity correlation")
	}
}
