package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func jarviJob51HTTPProfile(t *testing.T, c jarviJob51InventoryCase) (map[string]string, queue.GreenhouseMonitorProfile, api.SmallProviderOptions) {
	t.Helper()
	config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}
	o, e := api.SmallProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
	if e != nil {
		t.Fatal(e)
	}
	return config, queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}, o
}

func TestJarviAndJob51OriginalInventoryThroughHTTP(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			config, p, o := jarviJob51HTTPProfile(t, c)
			exchanges := map[string]string{}
			for _, x := range c.Exchanges {
				exchanges[x.URL] = x.Body
			}
			var mu sync.Mutex
			called := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				resource := "https://" + r.Host + r.URL.RequestURI()
				mu.Lock()
				called[resource]++
				body, ok := exchanges[resource]
				mu.Unlock()
				if !ok || !o.ResourceMatches(resource) || r.Method != "GET" {
					t.Error("unbound public request")
					w.WriteHeader(400)
					return
				}
				if c.Provider == "jarvi" && r.Header.Get("X-Api-Key") != "public_fixture_key" {
					t.Error("public SDK header changed")
				}
				w.Header().Set("Content-Type", "application/json; charset=utf-8")
				fmt.Fprint(w, body)
			}))
			waits := 0
			out, e := FetchSmallProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
			if (e != nil) != c.Error || waits != 0 {
				t.Fatal("HTTP inventory outcome or retry changed", e, c.Error, waits)
			}
			if c.Error {
				if len(out.Jobs) != 0 {
					t.Fatal("failed inventory yielded prefix")
				}
				return
			}
			if len(out.Jobs) != len(c.Jobs) || out.Truncated != c.Truncated {
				t.Fatal("inventory or truncation changed")
			}
			for i, field := range c.Jobs {
				expected, e := secondaryRichJob(field)
				if locations, ok := field["locations"].([]any); ok {
					for _, v := range locations {
						expected.Locations = append(expected.Locations, v.(string))
					}
				}
				if c.Provider == "job51" {
					expected.SourceIdentity, _ = field["source_identity"].(string)
				}
				if e != nil || !reflect.DeepEqual(jsonNormalizedRichJob(t, out.Jobs[i]), jsonNormalizedRichJob(t, expected)) {
					t.Fatalf("HTTP canonical fields differ at %d: got=%s want=%s error=%v", i, mustProviderJSON(out.Jobs[i]), mustProviderJSON(expected), e)
				}
			}
			if len(called) != len(exchanges) {
				t.Fatal("HTTP inventory requests omitted")
			}
			for _, n := range called {
				if n != 1 {
					t.Fatal("valid public request repeated")
				}
			}
		})
	}
}

func TestJarviAndJob51PublisherRetryAndRedirectAuthority(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		if c.Mode != "empty" {
			continue
		}
		for _, mode := range []string{"reserved404", "reserved503", "body503", "incomplete-reserved", "foreign-redirect", "same-redirect", "loop", "transient", "malformed", "empty", "gone"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				config, p, o := jarviJob51HTTPProfile(t, c)
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					switch mode {
					case "reserved404", "reserved503":
						w.Header().Set("TDM-Reservation", "1")
						if mode == "reserved404" {
							w.WriteHeader(404)
						} else {
							w.WriteHeader(503)
						}
					case "body503":
						w.Header().Set("Content-Type", "text/html")
						w.WriteHeader(503)
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
					case "incomplete-reserved":
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("Content-Length", "9999")
						fmt.Fprint(w, "prefix")
					case "foreign-redirect":
						http.Redirect(w, r, "https://foreign.example/private", 302)
					case "loop":
						http.Redirect(w, r, o.ListingURL(), 302)
					case "same-redirect":
						if calls == 1 {
							http.Redirect(w, r, o.ListingURL(), 302)
							return
						}
						fmt.Fprint(w, c.Exchanges[0].Body)
					case "transient":
						if calls == 1 {
							w.WriteHeader(503)
							return
						}
						fmt.Fprint(w, c.Exchanges[0].Body)
					case "malformed":
						fmt.Fprint(w, "malformed")
					case "empty":
					case "gone":
						w.WriteHeader(404)
					}
				}))
				out, e := FetchSmallProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				if mode == "same-redirect" || mode == "transient" && c.Provider == "job51" {
					wantWaits := 0
					if mode == "transient" {
						wantWaits = 1
					}
					if e != nil || len(out.Jobs) != 0 || calls != 2 || waits != wantWaits {
						t.Fatal("bounded redirect/retry failed", e, calls, waits)
					}
					return
				}
				if e == nil || len(out.Jobs) != 0 {
					t.Fatal("failed public resource yielded postings", e)
				}
				if strings.Contains(mode, "reserved") || mode == "body503" {
					var reserved *policy.Reservation
					if !errors.As(e, &reserved) || calls != 1 || waits != 0 || out.Response == nil || !out.Response.reserved {
						t.Fatal("publisher evidence lost or repeated", e, calls, waits)
					}
				}
				wantCalls := 1
				if mode == "loop" {
					wantCalls = 21
				}
				if c.Provider == "job51" && mode == "empty" {
					wantCalls = 3
				}
				if calls != wantCalls {
					t.Fatal("transport attempts changed", calls, wantCalls)
				}
				if mode == "gone" {
					var de *DiscoveryError
					if !errors.As(e, &de) || (de.Kind == "provider_gone") != (c.Provider == "job51") {
						t.Fatal("provider terminal contract differs", e)
					}
				}
			})
		}
	}
}

func TestJob51ConcurrentReservationPreservesEvidence(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		if c.Provider != "job51" || c.Mode != "paged" {
			continue
		}
		config, p, _ := jarviJob51HTTPProfile(t, c)
		exchanges := map[string]string{}
		for _, x := range c.Exchanges {
			exchanges[x.URL] = x.Body
		}
		var mu sync.Mutex
		started := 0
		release := make(chan struct{})
		client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			resource := "https://" + r.Host + r.URL.RequestURI()
			if r.URL.Path == "/job_detail.php" {
				mu.Lock()
				started++
				n := started
				if n == 5 {
					close(release)
				}
				mu.Unlock()
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
				if n == 1 {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
					return
				}
			}
			fmt.Fprint(w, exchanges[resource])
		}))
		out, e := FetchSmallProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
		var reserved *policy.Reservation
		if !errors.As(e, &reserved) || len(out.Jobs) != 0 || out.Response == nil || !out.Response.reserved {
			t.Fatal("concurrent failure lost publisher evidence", e)
		}
	}
}

func TestJarviAndJob51CanceledInventoriesDiscardResults(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		if c.Mode != "rich" {
			continue
		}
		config, p, _ := jarviJob51HTTPProfile(t, c)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		calls := 0
		client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++ }))
		out, e := FetchSmallProvidersHTTP(ctx, client.client, p, config, func(context.Context, time.Duration) error { return nil })
		if !errors.Is(e, context.Canceled) || len(out.Jobs) != 0 || calls != 0 {
			t.Fatal("canceled inventory fetched/published", e, calls)
		}
	}
}

func jsonNormalizedRichJob(t *testing.T, job RichMonitorJob) any {
	t.Helper()
	body, e := json.Marshal(job)
	if e != nil {
		t.Fatal(e)
	}
	var value any
	if json.Unmarshal(body, &value) != nil {
		t.Fatal("canonical JSON unavailable")
	}
	return value
}

func TestJob51TextLimitCountsCharactersAndParsesOnce(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		if c.Provider != "job51" || c.Mode != "empty" {
			continue
		}
		for _, size := range []int{2_000_000, 5_000_000} {
			t.Run(fmt.Sprint(size), func(t *testing.T) {
				config, p, _ := jarviJob51HTTPProfile(t, c)
				body := `jsoncallback({"status":"1","resultbody":{"totalnum":0,"joblist":[],"note":"` + strings.Repeat("中", size) + `"}})`
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
					fmt.Fprint(w, body)
				}))
				out, err := FetchSmallProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				// Original fetch_text_page_with_retry slices decoded characters
				// before parsing. A valid Chinese body over five MB is accepted;
				// exceeding five million characters cuts the closing JSONP and fails.
				if (err != nil) != (size == 5_000_000) || len(out.Jobs) != 0 || calls != 1 || waits != 0 {
					t.Fatal("original text limit or parse/retry boundary changed", err, calls, waits)
				}
			})
		}
	}
}
