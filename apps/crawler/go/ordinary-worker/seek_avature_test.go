package worker

import (
	"context"
	"encoding/json"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func TestSeekAvatureHTTPCompleteInventoriesAndPublisherFailures(t *testing.T) {
	for _, provider := range []string{"seek", "avature"} {
		raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_" + provider + ".json")
		var records []json.RawMessage
		if e != nil || json.Unmarshal(raw, &records) != nil {
			t.Fatal(e)
		}
		var fixture struct {
			Name, Kind, Source string
			Pages              []json.RawMessage
			Output             json.RawMessage
			Error              bool
		}
		for _, r := range records {
			var c struct {
				Name, Kind, Source string
				Pages              []json.RawMessage
				Output             json.RawMessage
				Error              bool
			}
			if json.Unmarshal(r, &c) != nil {
				t.Fatal("reference")
			}
			if c.Kind == "inventory" && !c.Error {
				fixture = c
				break
			}
		}
		if len(fixture.Pages) != 2 {
			t.Fatal("complete two-page reference missing")
		}
		profile := queue.GreenhouseMonitorProfile{Provider: provider}
		if provider == "seek" {
			o, e := api.SeekOptionsFromMetadata(fixture.Source, "{}")
			if e != nil {
				t.Fatal(e)
			}
			profile.Profile, profile.Endpoint = "seek.advertiser-urls/v1", o.PageURL(1)
		} else {
			o, e := api.AvatureOptionsFromMetadata(fixture.Source, "{}")
			if e != nil {
				t.Fatal(e)
			}
			profile.Profile, profile.Endpoint = "avature.listing-urls/v1", o.Board.ListingURL()
		}
		for _, mode := range []string{"complete", "retry403", "retry-body", "reserved503", "late-reserved", "foreign-redirect", "late-broken", "empty", "media"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				calls := 0
				waits := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if r.Method != "GET" || r.Header.Get("Authorization") != "" {
						t.Error("private/unexpected request")
					}
					page := 0
					if provider == "seek" && r.URL.Query().Get("page") == "2" || provider == "avature" && r.URL.RawQuery != "" {
						page = 1
					}
					media := "application/json"
					if provider == "avature" {
						media = "text/html"
					}
					w.Header().Set("Content-Type", media)
					if mode == "reserved503" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "retry403" && calls == 1 {
						w.WriteHeader(403)
						w.Write([]byte("retry"))
						return
					}
					if mode == "retry-body" && calls == 1 {
						w.Header().Set("Content-Length", "100")
						w.Write([]byte("broken"))
						return
					}
					if mode == "foreign-redirect" {
						w.Header().Set("Location", "https://evil.example/SearchJobs")
						w.WriteHeader(301)
						return
					}
					if mode == "late-reserved" && page == 1 {
						w.Header().Set("TDM-Reservation", "1")
						return
					}
					if mode == "late-broken" && page == 1 {
						w.Write([]byte("broken"))
						return
					}
					if mode == "empty" {
						return
					}
					if mode == "media" {
						w.Header().Set("Content-Type", "text/plain")
					}
					body := fixture.Pages[page]
					if provider == "avature" {
						var html string
						if json.Unmarshal(body, &html) != nil {
							t.Error("HTML fixture")
						}
						body = []byte(html)
					}
					w.Write(body)
				}))
				out, e := FetchSeekAvatureHTTP(context.Background(), client.client, profile, map[string]string{"board_url": fixture.Source, "metadata": "{}", "monitor_needs_browser": "0"}, func(context.Context, time.Duration) error { waits++; return nil })
				success := mode == "complete" || mode == "retry403" || mode == "retry-body"
				if (e == nil) != success || !success && len(out.Jobs) > 0 {
					t.Fatal(out, e)
				}
				if strings.Contains(mode, "reserved") {
					var reservation *policy.Reservation
					if !errors.As(e, &reservation) || out.Response == nil || !out.Response.reserved {
						t.Fatal("reservation lost", e, out)
					}
				}
				if (mode == "retry403" || mode == "retry-body") && waits != 1 {
					t.Fatal("retry budget", waits)
				}
				if mode == "empty" && (calls != 3 || waits != 2) {
					t.Fatal("empty page became inventory", calls, waits)
				}
				if success {
					urls := []string{}
					for _, job := range out.Jobs {
						if !job.URLOnly {
							t.Fatal("URL-only contract changed")
						}
						urls = append(urls, job.URL)
					}
					sort.Strings(urls)
					var want []string
					if provider == "seek" {
						json.Unmarshal(fixture.Output, &want)
					} else {
						var v struct {
							URLs      []string
							Truncated bool
							Metadata  map[string]any
						}
						json.Unmarshal(fixture.Output, &v)
						want = v.URLs
						if out.Truncated != v.Truncated || !reflect.DeepEqual(out.MetadataUpdates, v.Metadata) {
							t.Fatal(out, v)
						}
					}
					if !reflect.DeepEqual(urls, want) {
						t.Fatal(urls, want)
					}
				}
			})
		}
	}
}
