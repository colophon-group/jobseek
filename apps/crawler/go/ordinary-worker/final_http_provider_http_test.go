package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type finalHTTPInventoryCase struct {
	Provider, Mode string
	Board          struct {
		URL      string `json:"board_url"`
		Metadata json.RawMessage
	}
	Exchanges []struct {
		URL, Method, Body string
		RequestBody       string `json:"request_body"`
		Headers           map[string]string
	}
	Jobs             []map[string]any
	Truncated, Error bool
}

func finalHTTPInventoryCases(t *testing.T) []finalHTTPInventoryCase {
	t.Helper()
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_final_http_provider_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []finalHTTPInventoryCase
	if json.Unmarshal(body, &cases) != nil || len(cases) != 35 {
		t.Fatal("original inventory corpus unavailable")
	}
	return cases
}

func finalHTTPFixtureProfile(t *testing.T, c finalHTTPInventoryCase) (map[string]string, queue.GreenhouseMonitorProfile, api.FinalHTTPProviderOptions) {
	t.Helper()
	config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}
	o, e := api.FinalHTTPProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
	if e != nil {
		t.Fatal(e)
	}
	return config, queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}, o
}

func finalHTTPRequestBodyEqual(contentType, got, want string) bool {
	var a, b any
	if strings.HasPrefix(contentType, "application/json") {
		if json.Unmarshal([]byte(got), &a) != nil || json.Unmarshal([]byte(want), &b) != nil {
			return false
		}
	} else if strings.HasPrefix(contentType, "application/x-www-form-urlencoded") {
		a, _ = url.ParseQuery(got)
		b, _ = url.ParseQuery(want)
	} else {
		a, b = got, want
	}
	return reflect.DeepEqual(a, b)
}

func TestFinalHTTPProvidersOriginalInventoryThroughHTTP(t *testing.T) {
	for _, c := range finalHTTPInventoryCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			config, p, o := finalHTTPFixtureProfile(t, c)
			used := make([]bool, len(c.Exchanges))
			var mu sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				resource := "https://" + r.Host + r.URL.RequestURI()
				body, e := io.ReadAll(io.LimitReader(r.Body, 1<<20))
				if e != nil {
					t.Error(e)
				}
				mu.Lock()
				defer mu.Unlock()
				for i, x := range c.Exchanges {
					if used[i] || resource != x.URL || r.Method != x.Method || !finalHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
						continue
					}
					if !o.ResourceMatches(resource) {
						t.Error("unbound fixture resource")
					}
					for k, v := range x.Headers {
						if k == "accept" && v == "*/*" {
							continue
						}
						if r.Header.Get(k) != v {
							t.Error("original public header changed", k)
						}
					}
					used[i] = true
					w.Header().Set("Content-Type", "application/json; charset=utf-8")
					if c.Provider == "fenbi" {
						w.Header().Set("Content-Type", "text/html; charset=utf-8")
					}
					fmt.Fprint(w, x.Body)
					return
				}
				t.Error("unmatched original exchange", resource, r.Method)
				w.WriteHeader(400)
			}))
			out, e := FetchFinalHTTPProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			if (e != nil) != c.Error {
				t.Fatal("original HTTP outcome changed", e, c.Error)
			}
			if c.Error {
				if len(out.Jobs) != 0 {
					t.Fatal("failed inventory yielded prefix")
				}
				return
			}
			for _, u := range used {
				if !u {
					t.Fatal("original HTTP inventory omitted request")
				}
			}
			if len(out.Jobs) != len(c.Jobs) || out.Truncated != c.Truncated {
				t.Fatal("inventory/truncation differs")
			}
			for i, fields := range c.Jobs {
				want, e := secondaryRichJob(fields)
				if e != nil {
					t.Fatal(e)
				}
				want.SourceIdentity, _ = fields["source_identity"].(string)
				if locations, ok := fields["locations"].([]any); ok {
					for _, v := range locations {
						want.Locations = append(want.Locations, v.(string))
					}
				}
				if !reflect.DeepEqual(jsonNormalizedRichJob(t, out.Jobs[i]), jsonNormalizedRichJob(t, want)) {
					t.Fatalf("HTTP canonical fields differ at %d", i)
				}
			}
		})
	}
}

func TestFinalHTTPProvidersPublisherRetryStatusAndCancellation(t *testing.T) {
	for _, c := range finalHTTPInventoryCases(t) {
		if c.Mode != "rich" {
			continue
		}
		for _, mode := range []string{"header-reserved-404", "header-reserved-503", "body-reserved-503", "foreign-redirect", "transient", "malformed", "empty", "gone", "accepted-201", "canceled"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				config, p, _ := finalHTTPFixtureProfile(t, c)
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/json")
					switch mode {
					case "header-reserved-404", "header-reserved-503":
						w.Header().Set("TDM-Reservation", "1")
						if mode == "header-reserved-404" {
							w.WriteHeader(404)
						} else {
							w.WriteHeader(503)
						}
						return
					case "body-reserved-503":
						w.Header().Set("Content-Type", "text/html")
						w.WriteHeader(503)
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
						return
					case "foreign-redirect":
						http.Redirect(w, r, "https://foreign.example/private", 302)
						return
					case "transient":
						if calls == 1 {
							w.WriteHeader(503)
							return
						}
					case "malformed":
						fmt.Fprint(w, "malformed")
						return
					case "empty":
						return
					case "gone":
						w.WriteHeader(404)
						return
					case "accepted-201":
						w.WriteHeader(201)
					}
					// Return only the first public resource: a complete downstream
					// inventory is checked separately by the original exchange corpus.
					fmt.Fprint(w, c.Exchanges[0].Body)
				}))
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if mode == "canceled" {
					cancel()
				}
				out, e := FetchFinalHTTPProvidersHTTP(ctx, client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				if strings.Contains(mode, "reserved") {
					var reservation *policy.Reservation
					if !errors.As(e, &reservation) || calls != 1 || waits != 0 || out.Response == nil || !out.Response.reserved {
						t.Fatal("publisher precedence/evidence changed", e, calls, waits)
					}
					return
				}
				if mode == "canceled" {
					if !errors.Is(e, context.Canceled) || calls != 0 || len(out.Jobs) != 0 {
						t.Fatal("canceled inventory made progress", e, calls)
					}
					return
				}
				if mode == "accepted-201" && c.Provider == "paynet" {
					if e != nil || len(out.Jobs) != 1 || calls != 1 {
						t.Fatal("original201 list rejected", e)
					}
					return
				}
				if mode == "malformed" || mode == "empty" {
					wantCalls := 1
					if (c.Provider == "paynet") || (c.Provider == "wecruit" && mode == "empty") {
						wantCalls = 3
					}
					if calls != wantCalls || e == nil || len(out.Jobs) != 0 {
						t.Fatal("original parsing/retry boundary changed", e, calls, wantCalls)
					}
					return
				}
				if mode == "foreign-redirect" || mode == "gone" {
					if calls != 1 || e == nil || len(out.Jobs) != 0 {
						t.Fatal("failed resource yielded jobs", e, calls)
					}
					return
				}
				if mode == "transient" {
					wantWaits := 0
					if c.Provider == "paynet" || c.Provider == "wecruit" {
						wantWaits = 1
					}
					if waits != wantWaits {
						t.Fatal("transient retry contract changed", waits)
					}
				}
			})
		}
	}
}

func TestFinalHTTPProvidersChildReservationAndCancellationDiscardInventory(t *testing.T) {
	for _, c := range finalHTTPInventoryCases(t) {
		if c.Provider == "paynet" || c.Provider == "fenbi" && c.Mode != "rich" || c.Provider != "fenbi" && c.Mode != "paged" {
			continue
		}
		for _, mode := range []string{"reserved", "canceled"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				config, p, o := finalHTTPFixtureProfile(t, c)
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				used := make([]bool, len(c.Exchanges))
				var mu sync.Mutex
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
					resource := "https://" + r.Host + r.URL.RequestURI()
					child := c.Provider == "fenbi" && resource != o.ListingURL() || c.Provider == "nowhiring" && strings.HasPrefix(r.URL.Path, "/api/jobs/") && r.URL.Path != "/api/jobs/search" || c.Provider == "wecruit" && strings.Contains(r.URL.Path, "/listPositionDetail/")
					if child {
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(503)
						} else {
							cancel()
							<-r.Context().Done()
						}
						return
					}
					mu.Lock()
					defer mu.Unlock()
					for i, x := range c.Exchanges {
						if !used[i] && resource == x.URL && r.Method == x.Method && finalHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
							used[i] = true
							fmt.Fprint(w, x.Body)
							return
						}
					}
					t.Error("unmatched original parent resource")
					w.WriteHeader(400)
				}))
				out, err := FetchFinalHTTPProvidersHTTP(ctx, client.client, p, config, func(context.Context, time.Duration) error { return nil })
				if err == nil || len(out.Jobs) != 0 {
					t.Fatal("failed child published successful prefix", err)
				}
				if mode == "reserved" {
					var reserved *policy.Reservation
					if !errors.As(err, &reserved) || out.Response == nil || !out.Response.reserved {
						t.Fatal("concurrent child reservation evidence lost", err)
					}
				} else if !errors.Is(err, context.Canceled) {
					t.Fatal("child cancellation lost", err)
				}
			})
		}
	}
}
