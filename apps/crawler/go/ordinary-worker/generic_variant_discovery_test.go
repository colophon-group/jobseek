package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"testing"
)

func TestGroupedGenericHTTPMatchesActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Provider string
			BoardURL       string `json:"board_url"`
			Metadata       json.RawMessage
			Pages          []struct {
				Body    string
				Status  int
				Headers map[string]string
			}
			Requests        []struct{ URL, Method, Cache, Format string }
			Error, Reserved bool
			Jobs            []map[string]any
		}
	}
	body, e := os.ReadFile("testdata/python_generic_variant_http.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 25 {
		t.Fatal("actual Python grouped HTTP proof missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			cfg := map[string]string{"board_slug": "fixture", "board_url": c.BoardURL, "crawler_type": c.Provider, "company_id": "00000000-0000-4000-8000-000000000002", "domain": c.Provider, "throttle_key": c.Provider, "monitor_needs_browser": "0", "scraper_needs_browser": "0", "check_interval_minutes": "60", "scrape_interval_hours": "24"}
			var md map[string]any
			if e = json.Unmarshal(c.Metadata, &md); e != nil {
				t.Fatal(e)
			}
			md["scraper_type"] = "skip"
			raw, _ := json.Marshal(md)
			cfg["metadata"] = string(raw)
			p, e := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", cfg)
			if e != nil {
				t.Fatal("implemented configuration not admitted", e)
			}
			calls := 0
			client := &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
				n := calls
				calls++
				if n >= len(c.Requests) {
					t.Errorf("unexpected request %d", n)
				} else {
					want := c.Requests[n]
					if r.URL.String() != want.URL || r.Method != want.Method || r.Header.Get("X-No-Cache") != want.Cache || r.Header.Get("X-Return-Format") != want.Format {
						t.Errorf("request differs: %s %s cache=%q format=%q expected=%+v", r.Method, r.URL.String(), r.Header.Get("X-No-Cache"), r.Header.Get("X-Return-Format"), want)
					}
				}
				page := c.Pages[min(n, len(c.Pages)-1)]
				response := richHTTPResponse(r, page.Status, page.Body, false)
				for k, v := range page.Headers {
					response.Header.Set(k, v)
				}
				return response, nil
			})}
			var found RichDiscovery
			switch c.Provider {
			case "dom":
				found, e = discoverDOMInventory(context.Background(), client, p, cfg)
			case "rss":
				found, e = DiscoverRichMonitor(context.Background(), client, p)
			case "inline":
				found, e = discoverInlineInventory(context.Background(), client, p, cfg)
			}
			if (e != nil) != c.Error || calls != len(c.Requests) || (found.Response != nil && found.Response.reserved) != c.Reserved {
				t.Fatalf("HTTP terminal differs: error=%v expected_error=%v calls=%d/%d reserved=%v/%v", e, c.Error, calls, len(c.Requests), found.Response != nil && found.Response.reserved, c.Reserved)
			}
			if e != nil {
				return
			}
			if c.Provider == "dom" {
				found.Jobs, e = applyFeedMonitorURLs(context.Background(), cfg, found.Jobs)
				if e != nil {
					t.Fatal(e)
				}
			}
			if len(found.Jobs) != len(c.Jobs) {
				t.Fatalf("job count differs %d/%d", len(found.Jobs), len(c.Jobs))
			}
			for n, job := range found.Jobs {
				got := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "date_posted": job.DatePosted}
				encoded, _ := json.Marshal(got)
				var decoded map[string]any
				_ = json.Unmarshal(encoded, &decoded)
				for key, value := range c.Jobs[n] {
					if _, ok := got[key]; ok && !reflect.DeepEqual(decoded[key], value) {
						t.Fatal(fmt.Sprintf("actual Python %s field differs for %s: %v/%v", c.Provider, key, decoded[key], value))
					}
				}
			}
		})
	}
}
