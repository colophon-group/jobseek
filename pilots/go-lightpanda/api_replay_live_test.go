package main

import (
	"context"
	"errors"
	"github.com/chromedp/cdproto/network"
	"net/http"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestLiveAPIReplayCaptureKeepsFirstResponseWhenCompletionOrderDiffers(t *testing.T) {
	o, err := api.BrowserReplayOptionsFromMetadata("https://example.com/careers", `{"browser":true,"api_url":"https://example.com/token/old/jobs?stored=1","api_url_match":"example.com/token/*/jobs","json_path":"jobs","url_field":"url"}`)
	if err != nil {
		t.Fatal(err)
	}
	c, err := newReplayCapture(o)
	if err != nil {
		t.Fatal(err)
	}
	defer c.erase()
	for _, id := range []string{"first", "second"} {
		u := "https://example.com/token/" + id + "/jobs?private=rotating"
		c.observe(&network.EventRequestWillBeSent{RequestID: network.RequestID(id), Type: network.ResourceTypeFetch, Request: &network.Request{URL: u, Method: "GET"}})
		c.observe(&network.EventResponseReceived{RequestID: network.RequestID(id), Type: network.ResourceTypeFetch, Response: &network.Response{URL: u, Status: 200}})
	}
	for _, id := range []string{"second", "first"} {
		c.observe(&network.EventLoadingFinished{RequestID: network.RequestID(id), EncodedDataLength: 100})
	}
	exchanges, err := c.exchanges(context.Background(), func(_ context.Context, id network.RequestID) ([]byte, error) {
		if id == "first" {
			return []byte("invalid JSON"), nil
		}
		return []byte(`{"jobs":[{"url":"https://example.com/job/1"}]}`), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	fresh, document, matched := api.SelectLiveBrowserReplayExchange(o, exchanges)
	if !matched || document != nil || fresh != "https://example.com/token/first/jobs?stored=1" {
		t.Fatal("later completed JSON replaced first response")
	}
	if o.PublicCaptureResource("https://example.com/token/first/jobs?private=rotating") != fresh {
		t.Fatal("private query exported")
	}
}

func TestLiveAPIReplayRetryPassCannotReuseInjectedRequest(t *testing.T) {
	c := replayTestCapture(t)
	defer c.erase()
	replayTestRequest(c, "injected", "https://example.com/api")
	replayTestResponse(c, "injected", network.Headers{})
	if err := c.beginLivePass(); err != nil {
		t.Fatal(err)
	}
	c.observe(&network.EventResponseReceived{RequestID: "injected", Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://example.com/api", Status: 200}})
	c.observe(&network.EventLoadingFinished{RequestID: "injected", EncodedDataLength: 100})
	x, err := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		t.Fatal("injected response reused after pass barrier")
		return nil, nil
	})
	if err != nil || len(x) != 0 {
		t.Fatal(err)
	}
	c.failure = policy.ErrSignals
	if !errors.Is(c.beginLivePass(), policy.ErrSignals) {
		t.Fatal("retry erased terminal policy failure")
	}
}

func TestLiveAPIReplayHTTPFallbackBindsFreshPathAndCloses(t *testing.T) {
	metadata := `{"browser":true,"api_url":"https://example.com/token/old/jobs?stored=1","api_url_match":"example.com/token/*/jobs","json_path":"jobs","url_field":"url"}`
	var task Task
	task, err := newAPIReplayTask("https://example.com/careers", metadata, func(ctx context.Context, fetch api.Fetch, usingHTTP bool) error {
		if !usingHTTP {
			t.Fatal("fresh HTTP fallback not accounted")
		}
		_, err := fetch(ctx, api.Request{Method: "GET", URL: "https://example.com/token/old/jobs?stored=1"})
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	closed, called := false, 0
	task.APIReplay.fallbackFactory = func(o api.BrowserReplayOptions) (api.Fetch, func(), error) {
		if o.Inventory.Endpoint != "https://example.com/token/fresh/jobs?stored=1" {
			t.Fatal("HTTP authority did not bind observed path")
		}
		return func(_ context.Context, r api.Request) (*api.Document, error) {
			called++
			if !o.Inventory.ResourceMatches(r.URL) {
				t.Fatal("HTTP used unbound request")
			}
			return api.Decode([]byte(`{"jobs":[{"url":"https://example.com/job/1"}]}`))
		}, func() { closed = true }, nil
	}
	err = converseLiveAPIReplay(context.Background(), task.APIReplay, http.Header{}, nil, func(context.Context, api.Request) (*api.Document, error) { return nil, errReplayCapture }, func(context.Context) ([]api.BrowserReplayExchange, error) {
		e, err := api.NewLiveBrowserReplayExchange("https://example.com/token/fresh/jobs?private=rotating", "GET", http.Header{}, []byte("invalid"))
		return []api.BrowserReplayExchange{e}, err
	}, func(context.Context, api.BrowserReplayOptions, api.Request) (*api.Document, error) {
		return nil, errReplayCapture
	})
	if err != nil || called != 1 || !closed {
		t.Fatal("fresh HTTP fallback or cleanup failed", err, called, closed)
	}
}

func TestLiveAPIReplayUsesStoredURLFirstThenFreshPathForWholeInventory(t *testing.T) {
	metadata := strings.Replace(replayControllerMetadata, `https://example.com/api`, `https://example.com/token/old/jobs`, 1)
	metadata = strings.Replace(metadata, `"method":"POST"`, `"api_url_match":"example.com/token/*/jobs","method":"GET"`, 1)
	var task Task
	var inventory api.Inventory
	task, err := newAPIReplayTask("https://example.com/careers", metadata, func(ctx context.Context, fetch api.Fetch, httpFallback bool) error {
		var err error
		inventory, err = api.DiscoverBrowserReplay(ctx, task.APIReplay.options, fetch, replayControllerJoin, httpFallback)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	stored, renewed, later := 0, 0, 0
	err = converseLiveAPIReplay(context.Background(), task.APIReplay, http.Header{}, nil, func(_ context.Context, r api.Request) (*api.Document, error) {
		stored++
		if r.URL != "https://example.com/token/old/jobs" {
			t.Fatal("initial path changed")
		}
		return nil, errReplayCapture
	}, func(context.Context) ([]api.BrowserReplayExchange, error) {
		renewed++
		e, err := api.NewBrowserReplayExchange("https://example.com/token/fresh/jobs?page=999", "GET", http.Header{}, []byte(`{"total":3,"jobs":[{"id":"1","title":"One"}]}`))
		return []api.BrowserReplayExchange{e}, err
	}, func(_ context.Context, o api.BrowserReplayOptions, r api.Request) (*api.Document, error) {
		later++
		if !o.Inventory.ResourceMatches(r.URL) || !strings.HasPrefix(r.URL, "https://example.com/token/fresh/jobs?page=") {
			t.Fatal("fresh pagination binding lost")
		}
		return api.Decode([]byte(`{"total":3,"jobs":[{"id":"` + string(rune('1'+later)) + `","title":"Later"}]}`))
	})
	if err != nil || stored != 1 || renewed != 1 || later != 2 || len(inventory.Jobs) != 3 || inventory.Truncated {
		t.Fatal(err, stored, renewed, later, len(inventory.Jobs))
	}
}

func TestLiveAPIReplayDoesNotRediscoverAfterCaptureOrPublisherDenial(t *testing.T) {
	for _, captured := range []bool{false, true} {
		task, _ := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(ctx context.Context, fetch api.Fetch, _ bool) error {
			_, err := fetch(ctx, api.Request{Method: "POST", URL: "https://example.com/api"})
			return err
		})
		var first *api.Document
		if captured {
			first, _ = api.Decode([]byte(`{"jobs":[{"id":"1"}]}`))
		}
		denial := &policy.Reservation{URL: "https://example.com/api", Source: "header"}
		renewed := 0
		err := converseLiveAPIReplay(context.Background(), task.APIReplay, http.Header{}, first, func(context.Context, api.Request) (*api.Document, error) { return nil, denial }, func(context.Context) ([]api.BrowserReplayExchange, error) { renewed++; return nil, errReplayCapture }, func(context.Context, api.BrowserReplayOptions, api.Request) (*api.Document, error) {
			return nil, denial
		})
		if renewed != 0 || !captured && !errors.Is(err, denial) || captured && err != nil {
			t.Fatal(err, renewed)
		}
	}
}
