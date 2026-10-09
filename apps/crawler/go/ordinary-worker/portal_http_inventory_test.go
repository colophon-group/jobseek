package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type portalInventoryCase struct {
	Provider, Name, Status string
	CanonicalHTML          []string `json:"canonical_html"`
	Board                  struct {
		URL      string `json:"board_url"`
		Metadata json.RawMessage
	}
	Expected  []map[string]any
	Chunks    [][]map[string]any
	Exchanges []struct {
		Method, URL, Body string
		Headers           map[string]string
		Response          struct {
			Status  int
			Body    string
			Headers map[string]string
		}
	}
}

func portalInventoryCases(t *testing.T) []portalInventoryCase {
	t.Helper()
	body, err := os.ReadFile("../api-sniffer-monitor/testdata/python_portal_http_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []portalInventoryCase
	if json.Unmarshal(body, &cases) != nil || len(cases) != 26 {
		t.Fatal("original four-provider inventory corpus unavailable")
	}
	return cases
}

func portalFixture(t *testing.T, c portalInventoryCase) (map[string]string, queue.GreenhouseMonitorProfile) {
	t.Helper()
	o, err := api.PortalHTTPProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
	if err != nil {
		t.Fatal(err)
	}
	return map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}, queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.Listing}
}

func portalURLEqual(a, b string) bool {
	u, err := url.Parse(a)
	if err != nil {
		return false
	}
	v, err := url.Parse(b)
	if err != nil {
		return false
	}
	return u.Scheme == v.Scheme && u.Host == v.Host && u.EscapedPath() == v.EscapedPath() && reflect.DeepEqual(u.Query(), v.Query())
}

func comparePortalJobs(t *testing.T, provider string, got []RichMonitorJob, fields []map[string]any) {
	t.Helper()
	if len(got) != len(fields) {
		t.Fatalf("original inventory cardinality differs: %d/%d", len(got), len(fields))
	}
	for index, original := range fields {
		var want RichMonitorJob
		if provider == "infoniqa" {
			want = RichMonitorJob{URL: original["url"].(string), URLOnly: true}
		} else {
			var err error
			want, err = secondaryRichJob(original)
			if err != nil {
				t.Fatal(err)
			}
			if locations, ok := original["locations"].([]any); ok {
				for _, value := range locations {
					want.Locations = append(want.Locations, value.(string))
				}
			}
			want.Hybrid = provider == "pageup"
		}
		if !reflect.DeepEqual(jsonNormalizedRichJob(t, got[index]), jsonNormalizedRichJob(t, want)) {
			t.Fatalf("original complete record differs at %d", index)
		}
	}
}

func TestPortalHTTPProvidersActualOriginalInventoriesAndPagePrefixes(t *testing.T) {
	for _, c := range portalInventoryCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			config, p := portalFixture(t, c)
			used := make([]bool, len(c.Exchanges))
			var mu sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				resource := "https://" + r.Host + r.URL.RequestURI()
				body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
				if err != nil {
					t.Error(err)
				}
				mu.Lock()
				defer mu.Unlock()
				for index, x := range c.Exchanges {
					if used[index] || r.Method != x.Method || !portalURLEqual(resource, x.URL) || !finalHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.Body) {
						continue
					}
					for key, value := range x.Headers {
						if r.Header.Get(key) != value {
							t.Error("original request header differs", key)
						}
					}
					used[index] = true
					for key, value := range x.Response.Headers {
						w.Header().Set(key, value)
					}
					status := x.Response.Status
					if status == 0 {
						status = 200
					}
					w.WriteHeader(status)
					fmt.Fprint(w, x.Response.Body)
					return
				}
				t.Error("unexpected original HTTP request", r.Method, resource)
				w.WriteHeader(400)
			}))
			chunks := [][]RichMonitorJob{}
			out, err := FetchPortalHTTPProviders(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil }, func(jobs []RichMonitorJob) error { chunks = append(chunks, jobs); return nil })
			if (err != nil) != (c.Status == "failed") {
				t.Fatal("original inventory outcome differs", err, c.Status)
			}
			for _, value := range used {
				if !value {
					t.Fatal("original HTTP exchange omitted")
				}
			}
			if len(chunks) != len(c.Chunks) {
				t.Fatal("validated original page prefix differs", len(chunks), len(c.Chunks))
			}
			for index, chunk := range chunks {
				comparePortalJobs(t, c.Provider, chunk, c.Chunks[index])
			}
			if err != nil {
				if len(out.Jobs) != 0 {
					t.Fatal("failed inventory returned successful complete prefix")
				}
				return
			}
			comparePortalJobs(t, c.Provider, out.Jobs, c.Expected)
		})
	}
}
