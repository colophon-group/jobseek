package main

import (
	"context"
	"net/http"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// The parser sees original logical resources. Fresh path binding is private
// to this target and never changes stored metadata or the claim fingerprint.
func converseLiveAPIReplay(ctx context.Context, task *apiReplayTask, headers http.Header, first *api.Document, stored api.Fetch, rediscover func(context.Context) ([]api.BrowserReplayExchange, error), live func(context.Context, api.BrowserReplayOptions, api.Request) (*api.Document, error)) error {
	options := task.options
	endpoint := options.Inventory.Endpoint
	initial := first == nil
	var liveHTTP api.Fetch
	var closeHTTP func()
	defer func() {
		if closeHTTP != nil {
			closeHTTP()
		}
	}()
	boundRequest := func(request api.Request) (api.BrowserReplayOptions, api.Request, error) {
		bound := options
		if endpoint != options.Inventory.Endpoint {
			mapped, err := options.LiveRequestURL(request.URL, endpoint)
			if err != nil {
				return bound, request, errReplayCapture
			}
			bound.Inventory.Endpoint, request.URL = endpoint, mapped
		}
		return bound, request, nil
	}
	browser := func(call context.Context, request api.Request) (*api.Document, error) {
		if !initial {
			bound, request, err := boundRequest(request)
			if err != nil {
				return nil, err
			}
			return live(call, bound, request)
		}
		initial = false
		document, err := stored(call, request)
		if err == nil && document != nil || replayTerminalError(err) {
			return document, err
		}
		exchanges, err := rediscover(call)
		if err != nil {
			return nil, err
		}
		fresh, document, matched := api.SelectLiveBrowserReplayExchange(options, exchanges)
		if !matched {
			return nil, errReplayCapture
		}
		endpoint = fresh
		if document != nil {
			return document, nil
		}
		bound, request, err := boundRequest(request)
		if err != nil {
			return nil, err
		}
		return live(call, bound, request)
	}
	fallback := func(call context.Context, request api.Request) (*api.Document, error) {
		bound, request, err := boundRequest(request)
		if err != nil {
			return nil, err
		}
		if endpoint == options.Inventory.Endpoint && task.fallback != nil {
			return task.fallback(call, request)
		}
		if liveHTTP == nil {
			if task.fallbackFactory == nil {
				return nil, errReplayCapture
			}
			liveHTTP, closeHTTP, err = task.fallbackFactory(bound)
			if err != nil {
				return nil, err
			}
		}
		return liveHTTP(call, request)
	}
	return converseAPIReplay(ctx, task, headers, first, browser, fallback)
}
