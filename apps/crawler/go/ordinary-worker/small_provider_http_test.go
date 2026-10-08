package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestSmallProvidersOriginalPythonHTTPRequests(t *testing.T) {
	for _, c := range smallProviderOracleCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Board.Metadata)
			config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(md), "monitor_needs_browser": "0"}
			o, e := api.SmallProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(md))
			if e != nil {
				t.Fatal(e)
			}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls >= len(c.Requests) {
					t.Error("more requests than original Python")
				}
				expected := c.Requests[min(calls, len(c.Requests)-1)]
				want, _ := url.Parse(expected.URL)
				if r.Method != expected.Method || r.URL.Path != want.Path || r.URL.Query().Encode() != want.Query().Encode() {
					t.Error("request shape differs")
				}
				for k, v := range expected.Headers {
					if r.Header.Get(k) != v {
						t.Error("request header differs", k)
					}
				}
				body := c.Pages[min(calls, len(c.Pages)-1)]
				status := 200
				if c.Statuses != nil {
					status = c.Statuses[min(calls, len(c.Statuses)-1)]
				}
				calls++
				if status == 0 {
					panic(http.ErrAbortHandler)
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(status)
				w.Write(body)
			}))
			waits := 0
			out, e := FetchSmallProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
			if (e != nil) != c.Error || calls != len(c.Requests) {
				t.Fatal("original HTTP result or request count differs", e, c.Error, calls, len(c.Requests))
			}
			if e == nil && (len(out.Jobs) != len(c.Expected.Jobs) || out.Truncated != c.Expected.Truncated) {
				t.Fatal("original inventory/truncation differs")
			}
			if c.Provider == "seamlesshiring" && waits != 0 {
				t.Fatal("new retry added")
			}
		})
	}
}
func TestSmallProvidersPublisherReservationAndRedirects(t *testing.T) {
	sources := map[string]string{"cnstaff": "https://tenant.cnstaff.com/recruit", "jobbank104": "https://www.104.com.tw/company/abcde", "seamlesshiring": "https://tenant.seamlesshiring.com/"}
	for provider, source := range sources {
		for _, mode := range []string{"reserved404", "reserved503", "body503", "incomplete-reserved", "foreign-redirect", "same-redirect", "loop"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				o, e := api.SmallProviderOptionsFromMetadata(provider, source, "{}")
				if e != nil {
					t.Fatal(e)
				}
				p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
				c := map[string]string{"crawler_type": provider, "board_url": source, "metadata": "{}", "monitor_needs_browser": "0"}
				calls := 0
				waits := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					switch mode {
					case "reserved404", "reserved503":
						w.Header().Set("TDM-Reservation", "1")
						status := 503
						if mode == "reserved404" {
							status = 404
						}
						w.WriteHeader(status)
					case "body503":
						w.Header().Set("Content-Type", "text/html")
						w.WriteHeader(503)
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
					case "incomplete-reserved":
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("Content-Length", "9999")
						fmt.Fprint(w, "partial")
					case "foreign-redirect":
						http.Redirect(w, r, "https://foreign.example/private", 302)
					case "loop":
						http.Redirect(w, r, o.ListingURL(), 302)
					case "same-redirect":
						if calls == 1 {
							http.Redirect(w, r, o.ListingURL()+"?page=1", 302)
							return
						}
						switch provider {
						case "cnstaff":
							fmt.Fprint(w, `{"total":0,"page":{"now":1,"total":0},"list":[]}`)
						case "jobbank104":
							fmt.Fprint(w, `{"data":{"totalCount":0,"totalPages":0,"page":1,"pageSize":100,"list":{"normalJobs":[]}}}`)
						case "seamlesshiring":
							fmt.Fprint(w, `{"data":{"jobs":{"total":0,"data":[]}}}`)
						}
					}
				}))
				out, e := FetchSmallProvidersHTTP(context.Background(), client.client, p, c, func(context.Context, time.Duration) error { waits++; return nil })
				if mode == "same-redirect" {
					if e != nil || len(out.Jobs) != 0 || calls != 2 || waits != 0 {
						t.Fatal("bounded first-party redirect failed", e, calls, waits)
					}
					return
				}
				if e == nil || len(out.Jobs) != 0 || waits != 0 {
					t.Fatal("denied resource became partial success or retry", e, calls, waits)
				}
				if strings.Contains(mode, "reserved") || mode == "body503" {
					var reservation *policy.Reservation
					if !errors.As(e, &reservation) {
						t.Fatal("publisher evidence swallowed", e)
					}
					if calls != 1 {
						t.Fatal("reserved request repeated")
					}
				}
				if mode == "loop" && calls != 21 {
					t.Fatal("redirect bound changed", calls)
				}
			})
		}
	}
}
