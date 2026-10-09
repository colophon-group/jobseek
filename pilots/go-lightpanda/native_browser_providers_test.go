//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestNativeBrowserServiceCleanupPrefixGoneAndPolicy(t *testing.T) {
	for _, provider := range []string{"darwinbox", "bytedance"} {
		for _, mode := range []string{"success", "zero", "later-failure", "gone", "publisher", "cleanup"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				board := "https://airtel.darwinbox.in/ms/candidate/careers"
				if provider == "bytedance" {
					board = "https://joinbytedance.com/search"
				}
				request := replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: board, Metadata: json.RawMessage(`{"scraper_type":"skip"}`), Provider: provider, TimeoutMS: 15000}
				calls := 0
				cleaned := false
				execution := &runtimeV1ServiceExecution{dayforceRun: func(ctx context.Context, _ Config, task Task) (Result, error) {
					if task.APIReplay.nativeProvider != provider {
						t.Fatal("provider binding")
					}
					err := converseNativeBrowser(ctx, task.APIReplay, func(_ context.Context, r api.Request) (*api.Document, error) {
						calls++
						if mode == "publisher" {
							return nil, &policy.Reservation{URL: r.URL, Source: "header"}
						}
						if mode == "gone" {
							return nil, &replayStatusError{status: 404}
						}
						if mode == "later-failure" && calls > 1 {
							return api.Decode([]byte(`{"invalid":true}`))
						}
						if provider == "darwinbox" {
							var q map[string]any
							json.Unmarshal([]byte(r.Body), &q)
							page := int(q["page"].(float64))
							n, total := 1, 1
							if mode == "later-failure" {
								n, total = 100, 101
							}
							if mode == "zero" {
								n, total = 0, 0
							}
							rows := []any{}
							for i := 0; i < n; i++ {
								rows = append(rows, map[string]any{"id": fmt.Sprint((page-1)*100 + i + 1), "title": "Role", "jd": "&lt;p&gt;Build&lt;/p&gt;"})
							}
							b, _ := json.Marshal(map[string]any{"status": "success", "job_counts": total, "data": rows})
							return api.Decode(b)
						}
						var q map[string]any
						json.Unmarshal([]byte(r.Body), &q)
						n, total := 1, 1
						if mode == "later-failure" {
							n, total = 1000, 1001
						}
						if mode == "zero" {
							n, total = 0, 0
						}
						rows := []any{}
						for i := 0; i < n; i++ {
							rows = append(rows, map[string]any{"id": fmt.Sprint(i + 1), "title": "Role", "description": "Build", "city_info": map[string]string{"en_name": "Zurich"}})
						}
						b, _ := json.Marshal(map[string]any{"code": 0, "data": map[string]any{"count": total, "job_post_list": rows}})
						return api.Decode(b)
					})
					if err != nil {
						return Result{}, err
					}
					if mode == "cleanup" {
						return Result{}, errCleanupUnproved
					}
					cleaned = true
					return Result{apiReplaySessionSettled: true}, nil
				}}
				response, e := execution.executeAPIReplay(context.Background(), request)
				if e != nil || !response.Valid() {
					t.Fatal(e, response.Outcome)
				}
				want := "success"
				if mode == "cleanup" {
					want = "failed"
				}
				if mode == "publisher" {
					want = "publisher_reserved"
				}
				if mode == "gone" {
					want = "failed"
					if provider == "darwinbox" {
						want = "provider_gone"
					}
				}
				if mode == "later-failure" {
					want = "failed"
					if provider == "darwinbox" {
						want = "partial"
					}
				}
				if response.Outcome != want {
					t.Fatal("outcome", response.Outcome, want)
				}
				if want == "partial" {
					var inv api.Inventory
					json.Unmarshal(response.Inventory, &inv)
					if !cleaned || len(inv.Jobs) != 100 || inv.Truncated {
						t.Fatal("provisional prefix/absence authority")
					}
				}
				if mode == "publisher" && calls != 1 {
					t.Fatal("publisher denial retried")
				}
				if mode == "gone" && calls != 1 {
					t.Fatal("permanent status retried")
				}
				if (want == "success" || want == "provider_gone") && !cleaned {
					t.Fatal("result before cleanup")
				}
			})
		}
	}
}
func TestNativeBrowserRequestScopeAndClosedConversation(t *testing.T) {
	board := "https://jobs.bytedance.com/experienced/position"
	var saved api.Fetch
	task, e := newNativeBrowserTask("bytedance", board, `{}`, func(ctx context.Context, fetch api.Fetch, _ bool) error {
		saved = fetch
		p, _ := api.ByteDanceOptionsFromURL(board)
		if _, e := fetch(ctx, p.PageRequest(0, []string{"rd"})); e != nil {
			return e
		}
		foreign := p.PageRequest(0, nil)
		foreign.URL += "?foreign=1"
		if _, e := fetch(ctx, foreign); e == nil {
			t.Fatal("foreign URL admitted")
		}
		foreign = p.PageRequest(0, nil)
		foreign.Headers.Set("Authorization", "foreign")
		if _, e := fetch(ctx, foreign); e == nil {
			t.Fatal("foreign headers admitted")
		}
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	if e = converseNativeBrowser(context.Background(), task.APIReplay, func(context.Context, api.Request) (*api.Document, error) { return api.Decode([]byte(`{"code":0}`)) }); e != nil {
		t.Fatal(e)
	}
	p, _ := api.ByteDanceOptionsFromURL(board)
	if _, e := saved(context.Background(), p.PageRequest(0, nil)); e == nil {
		t.Fatal("callback survived browser scope")
	}
	request := replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: board, Metadata: json.RawMessage(`{}`), Provider: "unknown", TimeoutMS: 1000}
	if request.Valid() {
		t.Fatal("unknown provider protocol")
	}
	var reservation *policy.Reservation
	task, e = newNativeBrowserTask("bytedance", board, `{}`, func(ctx context.Context, fetch api.Fetch, _ bool) error {
		_, _ = fetch(ctx, p.PageRequest(0, nil))
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	e = converseNativeBrowser(context.Background(), task.APIReplay, func(context.Context, api.Request) (*api.Document, error) {
		return nil, &policy.Reservation{URL: p.Endpoint, Source: "header"}
	})
	if !errors.As(e, &reservation) {
		t.Fatal("swallowed publisher denial")
	}
}
