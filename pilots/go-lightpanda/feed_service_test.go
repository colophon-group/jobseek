//go:build !densitybench

package main

import (
	"context"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
	"strings"
	"testing"
	"time"
)

func feedRequestFixture() feed.Request {
	return feed.Request{Protocol: feed.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), FeedURL: "https://example.test/feed", PageParameter: "page", Start: 1, Increment: 1, MaxPages: 3, Wait: "domcontentloaded", NavigationTimeoutMS: 30000, TimeoutMS: 10000}
}
func TestFeedPinnedPagesHoldCapacityUntilCleanup(t *testing.T) {
	f := newServiceTLSFixture(t)
	entered := make(chan struct{})
	cleanup := make(chan struct{})
	requests := []string{}
	execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: f.server.serviceEgressPolicy.egressPolicy}, func(ctx context.Context, c Config, task Task) (Result, error) {
		if validateTask(task) != nil || task.Feed == nil {
			t.Error("invalid feed task")
		}
		result, e := task.Feed.converse(ctx, func(_ context.Context, endpoint string) (Result, error) {
			requests = append(requests, endpoint)
			return Result{Status: 200, FinalURL: endpoint, ResponseBody: []byte(`<rss><item><![CDATA[<p>Engineer</p>]]></item></rss>`), ResourcePolicy: &runtimev1.ResourcePolicySignals{}}, nil
		})
		close(entered)
		select {
		case <-cleanup:
		case <-ctx.Done():
			return Result{}, ctx.Err()
		}
		return result, e
	})
	if e != nil {
		t.Fatal(e)
	}
	service, address, stop := startRuntimeV1ServiceTest(t, f, execution)
	defer stop()
	client := dayforceClientFixture(t, f, address)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	held, e := client.Reserve(ctx)
	if e != nil {
		t.Fatal(e)
	}
	session, e := held.StartFeed(ctx, feedRequestFixture())
	if e != nil {
		t.Fatal(e)
	}
	defer session.Close()
	if len(service.slots) != 1 {
		t.Fatal("feed lost C4 slot")
	}
	for _, page := range []int{1, 2} {
		body, e := session.Page(page)
		if e != nil {
			t.Fatal(e)
		}
		var result runtimev1.BrowserResult
		if proto.Unmarshal(body, &result) != nil || result.GetSuccess() == nil || len(result.GetSuccess().Captures) != 1 {
			t.Fatal("raw page lost", &result)
		}
	}
	done := make(chan error, 1)
	go func() { done <- session.Finish(true) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("cleanup not reached")
	}
	select {
	case <-done:
		t.Fatal("success escaped before cleanup")
	case <-time.After(20 * time.Millisecond):
	}
	close(cleanup)
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	if len(requests) != 2 || requests[0] != "https://example.test/feed?page=1" || requests[1] != "https://example.test/feed?page=2" {
		t.Fatal(requests)
	}
}
func TestFeedDisconnectCancelsLivePageAndDisposesSession(t *testing.T) {
	f := newServiceTLSFixture(t)
	fetching := make(chan struct{})
	disposed := make(chan struct{})
	execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: f.server.serviceEgressPolicy.egressPolicy}, func(ctx context.Context, _ Config, task Task) (Result, error) {
		defer close(disposed)
		return task.Feed.converse(ctx, func(ctx context.Context, _ string) (Result, error) {
			close(fetching)
			<-ctx.Done()
			return Result{}, ctx.Err()
		})
	})
	if e != nil {
		t.Fatal(e)
	}
	_, address, stop := startRuntimeV1ServiceTest(t, f, execution)
	defer stop()
	client := dayforceClientFixture(t, f, address)
	held, e := client.Reserve(context.Background())
	if e != nil {
		t.Fatal(e)
	}
	session, e := held.StartFeed(context.Background(), feedRequestFixture())
	if e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { _, e := session.Page(1); done <- e }()
	select {
	case <-fetching:
	case <-time.After(time.Second):
		t.Fatal("page not started")
	}
	held.Close()
	select {
	case <-disposed:
	case <-time.After(time.Second):
		t.Fatal("disconnect did not dispose page")
	}
	if e = <-done; e == nil {
		t.Fatal("disconnected page accepted")
	}
	session.Close()
}
