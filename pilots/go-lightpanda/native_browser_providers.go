package main

import (
	"context"
	"errors"
	"math/rand/v2"
	"sync"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func newNativeBrowserTask(provider, board, metadata string, converse func(context.Context, api.Fetch, bool) error) (Task, error) {
	o, listing, e := api.NativeBrowserOptions(provider, board, metadata)
	if e != nil || converse == nil {
		return Task{}, errReplayCapture
	}
	wait := map[string]runtimev1.WaitCondition{"commit": runtimev1.WaitCondition_WAIT_CONDITION_COMMIT, "domcontentloaded": runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, "load": runtimev1.WaitCondition_WAIT_CONDITION_LOAD, "networkidle": runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE}[o.Wait]
	return Task{URL: listing, Navigation: &navigationOptions{wait: wait, timeout: time.Duration(o.TimeoutMS) * time.Millisecond}, APIReplay: &apiReplayTask{boardURL: listing, options: o, converse: converse, nativeProvider: provider, nativeMetadata: metadata}}, nil
}
func executeNativeBrowserConversation(ctx context.Context, task *apiReplayTask, finalURL string) error {
	if task.nativeProvider == "darwinbox" {
		expected, e := api.DarwinboxBoardFromURL(task.boardURL)
		actual, f := api.DarwinboxBoardFromURL(finalURL)
		if e != nil || f != nil || expected != actual {
			return errReplayCapture
		}
	} else {
		expected, e := api.ByteDanceOptionsFromURL(task.boardURL)
		actual, f := api.ByteDanceOptionsFromURL(finalURL)
		if e != nil || f != nil || expected != actual {
			return errReplayCapture
		}
	}
	return converseNativeBrowser(ctx, task, func(call context.Context, r api.Request) (*api.Document, error) {
		target := chromedp.FromContext(ctx)
		if target == nil || target.Target == nil {
			return nil, errReplayCapture
		}
		return fetchReplayCommand(cdp.WithExecutor(call, target.Target), api.NativeBrowserRequestOptions(task.options, r), r)
	})
}

// One affine scope, fixed public headers, original three-attempt retry classes,
// and the same target's cookies; no captured private credentials or HTTP fallback.
func converseNativeBrowser(ctx context.Context, task *apiReplayTask, fetch api.Fetch) error {
	if task == nil || fetch == nil || task.converse == nil {
		return errReplayCapture
	}
	var mu sync.Mutex
	open := true
	var terminal error
	defer func() { mu.Lock(); open = false; mu.Unlock() }()
	err := task.converse(ctx, func(call context.Context, r api.Request) (*api.Document, error) {
		mu.Lock()
		defer mu.Unlock()
		if !open || terminal != nil || ctx.Err() != nil || call.Err() != nil || !api.NativeBrowserRequestMatches(task.nativeProvider, task.boardURL, task.nativeMetadata, r) {
			return nil, errReplayCapture
		}
		var failure error
		for attempt := 0; attempt < 3; attempt++ {
			command, cancel := context.WithTimeout(call, 30*time.Second)
			stop := context.AfterFunc(ctx, cancel)
			d, e := fetch(command, r)
			stop()
			cancel()
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if call.Err() != nil {
				return nil, call.Err()
			}
			if e == nil && d != nil {
				return d, nil
			}
			failure = e
			if replayTerminalError(e) && !errors.Is(e, context.DeadlineExceeded) {
				terminal = e
				return nil, e
			}
			var status *replayStatusError
			if errors.As(e, &status) && status.status != 408 && status.status != 425 && status.status != 429 && (status.status < 500 || status.status > 599) {
				return nil, e
			}
			if attempt < 2 {
				timer := time.NewTimer(time.Duration(rand.Float64() * float64(time.Second) * float64(uint64(1)<<uint(attempt))))
				select {
				case <-ctx.Done():
					timer.Stop()
					return nil, ctx.Err()
				case <-call.Done():
					timer.Stop()
					return nil, call.Err()
				case <-timer.C:
				}
			}
		}
		if failure == nil {
			failure = errReplayCapture
		}
		return nil, failure
	}, false)
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
