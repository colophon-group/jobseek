package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestOriginalDOMNumberedTitleScanHTTP(t *testing.T) {
	var cases []struct {
		Name, Listing string
		BoardURL      string `json:"board_url"`
		Metadata      json.RawMessage
		Details       map[string]struct {
			Status int
			Body   string
		}
		Expected, Calls []string
		Error           bool
	}
	raw, e := os.ReadFile("../dom-detail/testdata/python_listing_title_scan.json")
	if e != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 14 {
		t.Fatal("actual original title oracle absent", e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "dom", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: c.BoardURL, BoardID: "fixture", EffectiveConfigSHA256: "bound"}
			o, e := queue.DOMMonitorOptions(config)
			if e != nil {
				t.Fatal(e)
			}
			calls := []string{c.BoardURL}
			var mu sync.Mutex
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				mu.Lock()
				calls = append(calls, r.URL.String())
				mu.Unlock()
				d, ok := c.Details[r.URL.Path]
				if !ok {
					t.Error("unexpected request", r.URL)
					d.Status = 404
				}
				return &http.Response{StatusCode: d.Status, Header: http.Header{"Content-Type": []string{"text/html; charset=utf-8"}}, Body: io.NopCloser(strings.NewReader(d.Body)), Request: r}, nil
			})}
			found, e := discoverDOMTitleScan(context.Background(), client, p, config, o, c.Listing, RichDiscovery{}, noSecondaryWait)
			if (e != nil) != c.Error {
				t.Fatal("original resolution changed", e, c.Error)
			}
			if e != nil && len(found.Jobs) != 0 {
				t.Fatal("partial inventory escaped")
			}
			if e == nil {
				urls := []string{}
				for _, j := range found.Jobs {
					if !j.URLOnly {
						t.Fatal("invented title content")
					}
					urls = append(urls, j.URL)
				}
				sort.Strings(urls)
				if !reflect.DeepEqual(urls, c.Expected) {
					t.Fatal(urls, c.Expected)
				}
			}
			// Cancellation may stop siblings after a decisive failure. Success still
			// scans the entire configured numbered space, including unlisted titles.
			if e == nil {
				sort.Strings(calls)
				if !reflect.DeepEqual(calls, c.Calls) {
					t.Fatal("incomplete numbered space", calls, c.Calls)
				}
			}
			if c.Name == "late-policy" && (found.Response == nil || !found.Response.reserved || !domVerificationResponseMatches(p, config, found.Response)) {
				t.Fatal("child publisher evidence lost")
			}
		})
	}
}

func TestDOMTitleScanRejectsInvalidAndMixedContracts(t *testing.T) {
	for _, extra := range []string{`"render":true`, `"link_selector":"a"`, `"empty_selector":".empty"`, `"pagination":{"param_name":"page"}`, `"fetch_url_transform":{"find":"careers","replace":"jobs"}`} {
		c := map[string]string{"crawler_type": "dom", "board_url": "https://example.com/careers", "metadata": `{"title_matched_url_scan":{"listing_title_selector":".title","detail_title_selector":"h4","url_template":"https://example.com/vacante-{index}"},` + extra + `}`}
		if extra == `"render":true` {
			c["monitor_needs_browser"] = "1"
		}
		if _, e := queue.DOMMonitorOptions(c); e == nil {
			t.Fatal("mixed original contract admitted", extra)
		}
	}
	for _, raw := range []string{`{"start":true}`, `{"max_scan":501}`, `{"start":0}`, `{"max_scan":1.5}`, `{"extra":true}`} {
		m := map[string]any{}
		json.Unmarshal([]byte(raw), &m)
		m["listing_title_selector"] = ".title"
		m["detail_title_selector"] = "h4"
		m["url_template"] = "https://example.com/vacante-{index}"
		if _, e := dom.ListingOptions(dom.Object{"title_matched_url_scan": m}, "https://example.com/careers"); e == nil {
			t.Fatal("invalid scan admitted", raw)
		}
	}
}
