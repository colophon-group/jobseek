package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestAccentureCapturedScopePreservesOpaqueBodyAndRejectsRetainedFetch(t *testing.T) {
	template := api.Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/jobsearch/result", Body: `{"startIndex":0,"maxResultSize":10,"opaque":"page-owned"}`, Headers: http.Header{"Content-Type": []string{"application/json"}}}
	var retained api.Fetch
	calls := 0
	task := &apiReplayTask{accentureConverse: func(ctx context.Context, fetch api.Fetch, captured api.Request) error {
		retained = fetch
		for _, offset := range []int{0, 500} {
			r, err := api.AccentureCapturedRequest(captured, offset)
			if err != nil {
				return err
			}
			if _, err = fetch(ctx, r); err != nil {
				return err
			}
		}
		return nil
	}}
	err := converseAccentureCaptured(context.Background(), task, template, func(_ context.Context, request api.Request) (*api.Document, error) {
		calls++
		document, decodeErr := api.Decode([]byte(request.Body))
		if decodeErr != nil {
			t.Fatal("captured JSON request invalid")
		}
		fields, _ := document.Value.(map[string]any)
		if fields["opaque"] != "page-owned" || fields["maxResultSize"] != json.Number("500") || request.Headers.Get("Content-Type") != "application/json" {
			t.Fatal("page-owned request changed")
		}
		return api.Decode([]byte(`{"data":[],"totalHits":{"total":0}}`))
	})
	if err != nil || calls != 2 {
		t.Fatal("captured pagination failed", err)
	}
	r, _ := api.AccentureCapturedRequest(template, 1000)
	if _, err = retained(context.Background(), r); err == nil || calls != 2 {
		t.Fatal("retained callback used a closed browser")
	}
}

func TestAccentureCapturedScopePolicyCannotBeIgnoredOrRetried(t *testing.T) {
	template := api.Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/jobsearch/result", Body: `startIndex=0&maxResultSize=10&opaque=page-owned`, Headers: http.Header{"Content-Type": []string{"application/x-www-form-urlencoded"}}}
	calls := 0
	task := &apiReplayTask{accentureConverse: func(ctx context.Context, fetch api.Fetch, captured api.Request) error {
		r, _ := api.AccentureCapturedRequest(captured, 0)
		_, _ = fetch(ctx, r)
		_, _ = fetch(ctx, r)
		return nil
	}}
	err := converseAccentureCaptured(context.Background(), task, template, func(context.Context, api.Request) (*api.Document, error) {
		calls++
		return nil, &policy.Reservation{URL: template.URL, Source: "header"}
	})
	var reserved *policy.Reservation
	if !errors.As(err, &reserved) || calls != 1 {
		t.Fatal("publisher denial became success or retry", err, calls)
	}
}

func TestAccentureCapturedScopeRejectsManufacturedOrSkippedRequests(t *testing.T) {
	template := api.Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/jobsearch/result", Body: `{"startIndex":0,"maxResultSize":10,"opaque":"page-owned"}`}
	for _, mode := range []string{"body", "endpoint", "skip"} {
		t.Run(mode, func(t *testing.T) {
			task := &apiReplayTask{accentureConverse: func(ctx context.Context, fetch api.Fetch, captured api.Request) error {
				r, _ := api.AccentureCapturedRequest(captured, 0)
				switch mode {
				case "body":
					r.Body = strings.Replace(r.Body, "page-owned", "manufactured", 1)
				case "endpoint":
					r.URL = "https://evil.test/api"
				case "skip":
					r, _ = api.AccentureCapturedRequest(captured, 500)
				}
				_, err := fetch(ctx, r)
				return err
			}}
			if err := converseAccentureCaptured(context.Background(), task, template, func(context.Context, api.Request) (*api.Document, error) {
				t.Fatal("unsupported request reached browser")
				return nil, nil
			}); err == nil {
				t.Fatal("unsupported request admitted")
			}
		})
	}
}
