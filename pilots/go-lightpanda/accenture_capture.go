package main

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/chromedp"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func newAccentureCapturedTask(board, metadata string, converse func(context.Context, api.Fetch, api.Request) error) (Task, error) {
	a, _, err := api.AccentureBrowserOptions(board, metadata)
	if err != nil || a.Endpoint != api.AccentureJobSearch || converse == nil {
		return Task{}, errReplayCapture
	}
	task, err := newNativeBrowserTask("accenture", board, metadata, func(context.Context, api.Fetch, bool) error { return errReplayCapture })
	if err == nil {
		task.APIReplay.converse = nil
		task.APIReplay.accentureConverse = converse
	}
	return task, err
}

func executeAccentureConversation(ctx context.Context, task *apiReplayTask, capture *replayCapture, finalURL string) error {
	if finalURL != task.boardURL {
		return errReplayCapture
	}
	call, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	ticker := time.NewTicker(25 * time.Millisecond)
	defer ticker.Stop()
	var id network.RequestID
	var headers http.Header
	for id == "" {
		capture.mu.Lock()
		failure := capture.failure
		id, headers = capture.templateID, capture.templateHeaders.Clone()
		capture.mu.Unlock()
		if failure != nil {
			return failure
		}
		if id != "" {
			break
		}
		select {
		case <-call.Done():
			return call.Err()
		case <-ticker.C:
		}
	}
	defer clear(headers)
	var body string
	err := chromedp.Run(call, chromedp.ActionFunc(func(command context.Context) error {
		var err error
		body, err = network.GetRequestPostData(id).Do(command)
		return err
	}))
	if err != nil || len(body) == 0 || len(body) > 1<<20 {
		return errReplayCapture
	}
	// Only the original content type is replayed. The held target supplies its
	// own cookies; captured request credentials never cross the service frame.
	template := api.Request{URL: task.options.Inventory.Endpoint, Method: "POST", Body: body, Headers: http.Header{"Content-Type": []string{headers.Get("Content-Type")}}}
	defer func() { template.Body = ""; clear(template.Headers) }()
	if template.Headers.Get("Content-Type") == "" {
		return errReplayCapture
	}
	target := chromedp.FromContext(ctx)
	if target == nil || target.Target == nil {
		return errReplayCapture
	}
	return converseAccentureCaptured(ctx, task, template, func(call context.Context, request api.Request) (*api.Document, error) {
		return fetchReplayCommand(cdp.WithExecutor(call, target.Target), api.NativeBrowserRequestOptions(task.options, request), request)
	})
}

func converseAccentureCaptured(ctx context.Context, task *apiReplayTask, template api.Request, fetch api.Fetch) error {
	if task == nil || task.accentureConverse == nil || fetch == nil {
		return errReplayCapture
	}
	var mu sync.Mutex
	open, offset := true, 0
	defer func() { mu.Lock(); open = false; mu.Unlock() }()
	// Reuse the fixed native three-attempt page policy after validating each
	// consecutive request against this target's own bounded body template.
	copyTask := *task
	copyTask.converse = func(call context.Context, retried api.Fetch, _ bool) error {
		return task.accentureConverse(call, func(call context.Context, request api.Request) (*api.Document, error) {
			mu.Lock()
			defer mu.Unlock()
			expected, err := api.AccentureCapturedRequest(template, offset)
			if !open || ctx.Err() != nil || call.Err() != nil || err != nil || request.Method != expected.Method || request.URL != expected.URL || request.Body != expected.Body || request.Probe {
				return nil, errReplayCapture
			}
			request.Headers = template.Headers.Clone()
			document, err := retried(call, request)
			if err == nil {
				offset += 500
			}
			return document, err
		}, template)
	}
	copyTask.nativeProvider = ""
	return converseNativeBrowserValidated(ctx, &copyTask, fetch, func(r api.Request) bool { return r.URL == template.URL && r.Method == "POST" })
}
