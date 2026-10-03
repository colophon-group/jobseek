package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type richRoundTrip func(*http.Request) (*http.Response, error)

func (f richRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func richHTTPResponse(r *http.Request, status int, body string, reserved bool) *http.Response {
	h := http.Header{}
	if reserved {
		h.Set("TDM-Reservation", "1")
		h.Set("TDM-Policy", "https://example.com/policy")
	}
	return &http.Response{Request: r, StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(body))}
}
func richProfileFixture(t *testing.T, kind string) queue.GreenhouseMonitorProfile {
	t.Helper()
	p, err := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", map[string]string{"board_slug": "fixture", "company_id": "00000000-0000-4000-8000-000000000002", "crawler_type": kind, "board_url": "https://example.com/careers", "metadata": `{"token":"fixture","scraper_type":"skip"}`, "check_interval_minutes": "60", "scrape_interval_hours": "24", "domain": kind, "throttle_key": kind, "monitor_needs_browser": "0", "scraper_needs_browser": "0"})
	if err != nil {
		t.Fatal(err)
	}
	return p
}
func TestRichAshbyDiscoveryUsesClaimEndpointAndRichFields(t *testing.T) {
	p := richProfileFixture(t, "ashby")
	calls := 0
	client := &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != p.Endpoint || r.Header.Get("Accept") != ordinaryAccept {
			t.Fatal("Ashby endpoint or default headers changed")
		}
		return richHTTPResponse(r, 200, `{"jobs":[{"jobUrl":"https://jobs.ashbyhq.com/fixture/listed","title":"Engineer","descriptionHtml":"<p>Build</p>","employmentType":"FullTime","workplaceType":"Remote","isListed":true},{"jobUrl":"https://jobs.ashbyhq.com/fixture/hidden","title":"Hidden","isListed":false}]}`, false), nil
	})}
	got, err := DiscoverRichMonitor(context.Background(), client, p)
	if err != nil || calls != 1 || len(got.Jobs) != 1 || got.Jobs[0].EmploymentType != "FullTime" || got.Jobs[0].JobLocationType != "remote" || got.Truncated {
		t.Fatalf("Ashby full inventory/field selection changed: %+v error=%v", got, err)
	}
}
func TestRichLeverDiscoveryPaginationFailureAndReservationNeverReturnsPartialInventory(t *testing.T) {
	jobs := []map[string]any{}
	for n := 0; n < 100; n++ {
		jobs = append(jobs, map[string]any{"hostedUrl": fmt.Sprintf("https://jobs.lever.co/fixture/%d", n), "text": "Engineer", "categories": map[string]any{"commitment": "Full-time"}, "workplaceType": "remote"})
	}
	body, _ := json.Marshal(jobs)
	for _, mode := range []string{"complete", "later_404", "later_transient", "later_json", "later_reserved", "first_404"} {
		t.Run(mode, func(t *testing.T) {
			p := richProfileFixture(t, "lever")
			calls := 0
			second := 0
			client := &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.Header.Get("Accept") != "application/json" || r.URL.Query().Get("limit") != "100" {
					t.Fatal("Lever JSON/page contract changed")
				}
				if r.URL.Query().Get("skip") == "0" {
					if mode == "first_404" {
						return richHTTPResponse(r, 404, "{}", false), nil
					}
					return richHTTPResponse(r, 200, string(body), false), nil
				}
				if r.URL.Query().Get("skip") != "100" {
					t.Fatal("unexpected page offset")
				}
				second++
				switch mode {
				case "complete":
					return richHTTPResponse(r, 200, `[{"hostedUrl":"https://jobs.lever.co/fixture/last","text":"Last"}]`, false), nil
				case "later_404":
					return richHTTPResponse(r, 404, "{}", false), nil
				case "later_transient":
					return richHTTPResponse(r, 503, "{}", false), nil
				case "later_json":
					return richHTTPResponse(r, 200, "invalid JSON", false), nil
				case "later_reserved":
					return richHTTPResponse(r, 200, "[]", true), nil
				}
				panic("unknown case")
			})}
			got, err := DiscoverRichMonitor(context.Background(), client, p)
			if mode == "complete" {
				if err != nil || calls != 2 || len(got.Jobs) != 101 || got.Jobs[0].EmploymentType != "Full-time" || got.Jobs[0].JobLocationType != "remote" {
					t.Fatalf("Lever pagination/fields changed: %+v error=%v", got, err)
				}
				return
			}
			if err == nil || len(got.Jobs) != 0 {
				t.Fatal("incomplete pagination delivered partial inventory")
			}
			if mode == "later_transient" || mode == "later_json" {
				if second != 3 {
					t.Fatal("Lever page retry budget changed", second)
				}
			}
			if mode == "later_404" && got.Response != nil {
				t.Fatal("later page404 became provider disappearance evidence")
			}
			if mode == "first_404" && (got.Response == nil || got.Response.Status() != 404) {
				t.Fatal("first page404 lost provider disappearance")
			}
			if mode == "later_reserved" && (got.Response == nil || !got.Response.Reserved() || got.Response.PolicyURL() == nil) {
				t.Fatal("later page publisher policy lost its completed resource")
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	client := &http.Client{Transport: richRoundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("cancelled inventory fetched"); return nil, nil })}
	if _, err := DiscoverRichMonitor(ctx, client, richProfileFixture(t, "lever")); !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation lost", err)
	}
}
