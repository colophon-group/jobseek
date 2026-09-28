package main

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

const networkIdlePeriod = 500 * time.Millisecond

var errNavigationTimeout = errors.New("navigation readiness timed out")

// navigationOptions is copied from a validated render-only plan. Fallback
// checks the current document; it can never cause a second Page.navigate.
type navigationOptions struct {
	wait     runtimev1.WaitCondition
	timeout  time.Duration
	fallback *navigationOptions
}

type navigationState struct {
	mu                          sync.Mutex
	mainFrame                   cdp.FrameID
	loader                      cdp.LoaderID
	committed, domReady, loaded bool
	requests                    map[network.RequestID]struct{}
	idleSince                   time.Time
	changed                     chan struct{}
}

func newNavigationState(frame cdp.FrameID) *navigationState {
	return &navigationState{mainFrame: frame, requests: make(map[network.RequestID]struct{}), changed: make(chan struct{}, 1)}
}

func (state *navigationState) signal() {
	select {
	case state.changed <- struct{}{}:
	default:
	}
}

func (state *navigationState) commit(loader cdp.LoaderID, now time.Time) {
	if loader == "" {
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	if loader != state.loader {
		state.loader = loader
		state.domReady, state.loaded = false, false
		state.idleSince = time.Time{}
	}
	state.committed = true
	if len(state.requests) == 0 && state.idleSince.IsZero() {
		state.idleSince = now
	}
	state.signal()
}

func validNavigationOptions(options *navigationOptions) bool {
	if options == nil {
		return true
	}
	validWait := func(wait runtimev1.WaitCondition) bool {
		return wait >= runtimev1.WaitCondition_WAIT_CONDITION_COMMIT && wait <= runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE
	}
	if !validWait(options.wait) || options.timeout <= 0 || options.timeout > 120*time.Second {
		return false
	}
	if fallback := options.fallback; fallback != nil {
		return validWait(fallback.wait) && fallback.wait != options.wait && fallback.timeout > 0 && fallback.timeout <= 5*time.Second && fallback.timeout <= options.timeout && fallback.fallback == nil
	}
	return true
}

func (state *navigationState) observe(event any, now time.Time) {
	if event, ok := event.(*page.EventFrameNavigated); ok {
		if event.Frame != nil && event.Frame.ID == state.mainFrame && event.Frame.URL != "about:blank" {
			state.commit(event.Frame.LoaderID, now)
		}
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	switch event := event.(type) {
	case *page.EventDomContentEventFired:
		if state.committed {
			state.domReady = true
		}
	case *page.EventLoadEventFired:
		if state.committed {
			state.domReady, state.loaded = true, true
		}
	case *network.EventRequestWillBeSent:
		// WebSocket connections are not pending HTTP resource loads. Redirects
		// reuse a request ID and must not count twice.
		if event.Type != network.ResourceTypeWebSocket {
			state.requests[event.RequestID] = struct{}{}
			state.idleSince = time.Time{}
		}
	case *network.EventLoadingFinished:
		if _, ok := state.requests[event.RequestID]; ok {
			delete(state.requests, event.RequestID)
			if len(state.requests) == 0 {
				state.idleSince = now
			}
		}
	case *network.EventLoadingFailed:
		if _, ok := state.requests[event.RequestID]; ok {
			delete(state.requests, event.RequestID)
			if len(state.requests) == 0 {
				state.idleSince = now
			}
		}
	default:
		return
	}
	state.signal()
}

func (state *navigationState) readiness(wait runtimev1.WaitCondition, now time.Time) (bool, time.Duration) {
	state.mu.Lock()
	defer state.mu.Unlock()
	if !state.committed {
		return false, 0
	}
	switch wait {
	case runtimev1.WaitCondition_WAIT_CONDITION_COMMIT:
		return true, 0
	case runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED:
		return state.domReady, 0
	case runtimev1.WaitCondition_WAIT_CONDITION_LOAD:
		return state.loaded, 0
	case runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE:
		if len(state.requests) != 0 || state.idleSince.IsZero() {
			return false, 0
		}
		remaining := networkIdlePeriod - now.Sub(state.idleSince)
		return remaining <= 0, max(remaining, time.Duration(0))
	default:
		return false, 0
	}
}

func waitForNavigation(ctx context.Context, state *navigationState, wait runtimev1.WaitCondition) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		ready, delay := state.readiness(wait, time.Now())
		if ready {
			return nil
		}
		var wake <-chan time.Time
		var timer *time.Timer
		if delay > 0 {
			timer = time.NewTimer(delay)
			wake = timer.C
		}
		select {
		case <-ctx.Done():
			if timer != nil {
				timer.Stop()
			}
			return ctx.Err()
		case <-state.changed:
		case <-wake:
		}
		if timer != nil {
			timer.Stop()
		}
	}
}

func navigateDocument(ctx context.Context, state *navigationState, options navigationOptions, navigate func(context.Context) error) error {
	primary, cancelPrimary := context.WithTimeout(ctx, options.timeout)
	err := navigate(primary)
	if err == nil {
		err = waitForNavigation(primary, state, options.wait)
	}
	cancelPrimary()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err == nil || !errors.Is(err, context.DeadlineExceeded) {
		return err
	}
	if options.fallback == nil {
		return errNavigationTimeout
	}
	fallback, cancelFallback := context.WithTimeout(ctx, options.fallback.timeout)
	defer cancelFallback()
	err = waitForNavigation(fallback, state, options.fallback.wait)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return errNavigationTimeout
	}
	return err
}
