package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func teamtailorRSSProfile(t *testing.T) queue.GreenhouseMonitorProfile {
	t.Helper()
	p, err := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", map[string]string{"board_slug": "fixture", "company_id": "00000000-0000-4000-8000-000000000002", "crawler_type": "rss", "board_url": "https://example.com/careers", "metadata": `{"preset":"teamtailor","feed_url":"https://example.com/jobs.rss","scraper_type":"skip"}`, "check_interval_minutes": "60", "scrape_interval_hours": "24", "domain": "rss", "throttle_key": "rss", "monitor_needs_browser": "0", "scraper_needs_browser": "0"})
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func teamtailorRSSPage(count int) string {
	var body strings.Builder
	body.WriteString(`<rss xmlns:tt="https://teamtailor.com/locations"><channel>`)
	for i := 0; i < count; i++ {
		fmt.Fprintf(&body, `<item><link>https://example.com/job/%d</link><title>Engineer</title><description>&lt;p&gt;Build&lt;/p&gt;</description><remoteStatus>fully</remoteStatus><tt:locations><tt:location><tt:city>Zurich</tt:city><tt:country>Switzerland</tt:country></tt:location></tt:locations></item>`, i)
	}
	body.WriteString(`</channel></rss>`)
	return body.String()
}

func TestNativeTeamtailorPaginationPreservesFieldsAndNeverReturnsPartialFailure(t *testing.T) {
	for _, mode := range []string{"complete", "later404", "later_reserved", "duplicate_flags", "nonliteral_flag"} {
		t.Run(mode, func(t *testing.T) {
			p := teamtailorRSSProfile(t)
			calls := 0
			client := &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.URL.Query().Get("per_page") != "100" || !richResponseMatches(p, r.URL.String()) {
					t.Fatal("RSS page binding changed")
				}
				if calls == 1 {
					return richHTTPResponse(r, 200, teamtailorRSSPage(100), false), nil
				}
				if r.URL.Query().Get("offset") != "100" {
					t.Fatal("RSS pagination offset changed")
				}
				if mode == "later404" {
					return richHTTPResponse(r, 404, "", false), nil
				}
				response := richHTTPResponse(r, 200, teamtailorRSSPage(0), mode == "later_reserved" || mode == "duplicate_flags")
				if mode == "duplicate_flags" {
					response.Header.Add("TDM-Reservation", "1")
				}
				if mode == "nonliteral_flag" {
					response.Header.Set("TDM-Reservation", "01")
				}
				return response, nil
			})}
			found, err := DiscoverRichMonitor(context.Background(), client, p)
			if calls != 2 {
				t.Fatal("page request count changed", calls)
			}
			if mode == "complete" || mode == "duplicate_flags" || mode == "nonliteral_flag" {
				if err != nil || len(found.Jobs) != 100 || found.Jobs[0].Description == nil || *found.Jobs[0].Description != "<p>Build</p>" || found.Jobs[0].JobLocationType != "remote" || fmt.Sprint(found.Jobs[0].Locations) != "[Zurich, Switzerland]" {
					t.Fatalf("RSS rich fields lost: %+v %v", found, err)
				}
			} else {
				if err == nil || len(found.Jobs) != 0 {
					t.Fatal("partial RSS inventory returned")
				}
				if mode == "later404" && found.Response != nil {
					t.Fatal("later RSS404 became provider-gone")
				}
				if mode == "later_reserved" && (found.Response == nil || !found.Response.Reserved() || found.Response.Endpoint() != "https://example.com/jobs.rss?offset=100&per_page=100") {
					t.Fatal("publisher policy lost actual RSS page")
				}
			}
		})
	}
	for _, endpoint := range []string{"https://other.example.com/jobs.rss?offset=0&per_page=100", "https://example.com/jobs.rss?offset=1&per_page=100", "https://example.com/jobs.rss?offset=0&per_page=100&extra=1", "https://example.com/jobs.rss?offset=0&offset=100&per_page=100"} {
		if richResponseMatches(teamtailorRSSProfile(t), endpoint) {
			t.Fatal("foreign or invalid RSS page matched", endpoint)
		}
	}
}
