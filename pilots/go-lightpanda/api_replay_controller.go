package main

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// The conversation receives public documents and a scoped fetch function.
// Captured request credentials stay in this target, including on later pages.
// Runtime admission still requires the service protocol and HTTP fallback.
type apiReplayTask struct {
	boardURL                       string
	options                        api.BrowserReplayOptions
	converse                       func(context.Context, api.Fetch, bool) error
	brassRingConverse              func(context.Context, api.BrassRingPageLoader) error
	fallback                       api.Fetch
	nativeProvider, nativeMetadata string
}

func (task *apiReplayTask) hasOneConversation() bool {
	count := 0
	if task.converse != nil {
		count++
	}
	if task.brassRingConverse != nil {
		count++
	}
	return count == 1
}

func readReplayResponseBody(ctx context.Context, id network.RequestID) ([]byte, error) {
	var body []byte
	err := chromedp.Run(ctx, chromedp.ActionFunc(func(call context.Context) error {
		var err error
		body, err = network.GetResponseBody(id).Do(call)
		if err != nil {
			return errReplayCapture
		}
		return nil
	}))
	return body, err
}

func newAPIReplayTask(boardURL, metadata string, converse func(context.Context, api.Fetch, bool) error) (Task, error) {
	o, err := api.BrowserReplayOptionsFromMetadata(boardURL, metadata)
	if err != nil || converse == nil {
		return Task{}, errReplayCapture
	}
	wait := map[string]runtimev1.WaitCondition{
		"commit":           runtimev1.WaitCondition_WAIT_CONDITION_COMMIT,
		"domcontentloaded": runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED,
		"load":             runtimev1.WaitCondition_WAIT_CONDITION_LOAD,
		"networkidle":      runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE,
	}[o.Wait]
	return Task{URL: boardURL, Navigation: &navigationOptions{wait: wait, timeout: time.Duration(o.TimeoutMS) * time.Millisecond}, APIReplay: &apiReplayTask{boardURL: boardURL, options: o, converse: converse}}, nil
}

func executeAPIReplayConversation(ctx context.Context, task *apiReplayTask, capture *replayCapture, status int, finalURL, html string, signals *runtimev1.ResourcePolicySignals) error {
	if task == nil || capture == nil || status < 200 || status >= 300 {
		return errReplayCapture
	}
	if err := policy.Check(signals, html, finalURL); err != nil {
		return err
	}
	if task.brassRingConverse != nil {
		capture.mu.Lock()
		failure := capture.failure
		capture.mu.Unlock()
		if failure != nil {
			return failure
		}
		return executeBrassRingConversation(ctx, task, finalURL)
	}
	if task.nativeProvider != "" {
		if task.nativeProvider == "darwinbox" {
			classification, e := dom.ClassifyRendered(html, dom.Object{}, finalURL)
			if e != nil || classification["classification"] != "okay" {
				return errReplayCapture
			}
		}
		capture.mu.Lock()
		capturedFailure := capture.failure
		capture.mu.Unlock()
		if capturedFailure != nil {
			return capturedFailure
		}
		failure := executeNativeBrowserConversation(ctx, task, finalURL)
		capture.mu.Lock()
		capturedFailure = capture.failure
		capture.mu.Unlock()
		if capturedFailure != nil {
			return capturedFailure
		}
		return failure
	}
	timer := time.NewTimer(time.Duration(task.options.SettleMS) * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
	}
	exchanges, err := capture.exchanges(ctx, readReplayResponseBody)
	if err != nil {
		return err
	}
	headers, first, matched, err := api.SelectBrowserReplayExchange(task.options, exchanges)
	if err != nil {
		return errReplayCapture
	}
	if !matched {
		headers = task.options.Inventory.Headers.Clone()
	}
	return converseAPIReplay(ctx, task, headers, first, func(call context.Context, request api.Request) (*api.Document, error) {
		target := chromedp.FromContext(ctx)
		if target == nil || target.Target == nil {
			return nil, errReplayCapture
		}
		return fetchReplayCommand(cdp.WithExecutor(call, target.Target), task.options, request)
	}, task.fallback)
}

// Serial commands and a closed scope prevent retained callbacks from using
// a target after the conversation has finished. An ignored policy failure
// still fails the entire conversation (including an optional size probe).
func converseAPIReplay(ctx context.Context, task *apiReplayTask, headers http.Header, first *api.Document, fetch api.Fetch, fallbacks ...api.Fetch) error {
	if task == nil || task.converse == nil || fetch == nil {
		return errReplayCapture
	}
	var mu sync.Mutex
	open, initial := true, true
	var terminal error
	defer func() {
		mu.Lock()
		defer mu.Unlock()
		open = false
		clear(headers)
	}()
	usingHTTP := false
	if first == nil {
		request := api.Request{Method: task.options.Inventory.Method, URL: task.options.Inventory.Endpoint, Body: task.options.Inventory.Body, Headers: replayHeaders(headers)}
		var err error
		first, err = fetch(ctx, request)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if replayTerminalError(err) {
			return err
		}
		if err != nil || first == nil {
			if len(fallbacks) != 1 || fallbacks[0] == nil {
				return errReplayCapture
			}
			fetch = fallbacks[0]
			usingHTTP = true
			first, err = fetch(ctx, request)
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if err != nil {
				return err
			}
			if first == nil {
				return errReplayCapture
			}
		}
	}
	err := task.converse(ctx, func(call context.Context, request api.Request) (*api.Document, error) {
		mu.Lock()
		defer mu.Unlock()
		if !open || terminal != nil {
			return nil, errReplayCapture
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if call.Err() != nil {
			return nil, call.Err()
		}
		o := task.options.Inventory
		if request.Method != o.Method || !o.ResourceMatches(request.URL) {
			return nil, errReplayCapture
		}
		request.Headers = replayHeaders(headers)
		if initial {
			initial = false
			if first != nil && !request.Probe && request.URL == o.Endpoint && request.Body == o.Body {
				return first, nil
			}
		}
		command, cancel := context.WithCancel(call)
		stop := context.AfterFunc(ctx, cancel)
		defer stop()
		defer cancel()
		document, err := fetch(command, request)
		if err != nil {
			// Public policy errors are preserved; fixed capture failures cannot
			// expose private expressions or captured headers.
			if replayTerminalError(err) {
				terminal = err
			}
		}
		return document, err
	}, usingHTTP)
	mu.Lock()
	defer mu.Unlock()
	if terminal != nil {
		return terminal
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}

func replayTerminalError(err error) bool {
	var reservation *policy.Reservation
	return errors.As(err, &reservation) || errors.Is(err, policy.ErrSignals) || errors.Is(err, errResourceLimit) || errors.Is(err, errReplayCredentialResponse) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}
