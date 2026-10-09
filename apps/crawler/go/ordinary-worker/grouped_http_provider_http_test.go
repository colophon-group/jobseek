package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type groupedHTTPInventoryCase struct {
	Provider, Mode string
	Board          struct {
		URL      string `json:"board_url"`
		Metadata json.RawMessage
	}
	Exchanges []struct {
		URL, Method, Body string
		Status            int
		RequestBody       string `json:"request_body"`
		Headers           map[string]string
		RequestHeaders    map[string]string `json:"request_headers"`
		ResponseHeaders   map[string]string `json:"response_headers"`
	}
	Jobs             json.RawMessage
	Truncated, Error bool
}

func groupedHTTPInventoryCases(t *testing.T) []groupedHTTPInventoryCase {
	t.Helper()
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_curately_inploi_jobconvo_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []groupedHTTPInventoryCase
	if json.Unmarshal(body, &cases) != nil || len(cases) != 36 {
		t.Fatal("original inventory corpus unavailable")
	}
	if directory := os.Getenv("JOBSEEK_GROUPED_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
		for slug, tag := range map[string]string{"bristol-myers-squibb-contractors": "verified2", "compass-group-uk-ireland": "verified3", "deloitte-brazil": "verified2"} {
			body, e := os.ReadFile(filepath.Join(directory, "native992-grouped-http-public-"+slug+"-"+tag+".json"))
			if e != nil {
				t.Fatal("private original capture missing", slug)
			}
			var c groupedHTTPInventoryCase
			var state struct{ Status string }
			if json.Unmarshal(body, &c) != nil || json.Unmarshal(body, &state) != nil || len(c.Exchanges) == 0 || state.Status != "complete" && state.Status != "failed" {
				t.Fatal("invalid private original capture", slug)
			}
			c.Error = state.Status == "failed"
			for i := range c.Exchanges {
				x := &c.Exchanges[i]
				if len(x.RequestHeaders) > 0 {
					x.ResponseHeaders = x.Headers
					x.Headers = x.RequestHeaders
				}
			}
			cases = append(cases, c)
		}
	}
	return cases
}

func groupedHTTPFixtureProfile(t *testing.T, c groupedHTTPInventoryCase) (map[string]string, queue.GreenhouseMonitorProfile, api.FinalHTTPProviderOptions) {
	t.Helper()
	config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}
	o, e := api.FinalHTTPProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
	if e != nil {
		t.Fatal(e)
	}
	return config, queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}, o
}

func groupedHTTPRequestBodyEqual(contentType, got, want string) bool {
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

func TestCuratelyInploiJobConvoOriginalInventoryThroughHTTP(t *testing.T) {
	for _, c := range groupedHTTPInventoryCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			config, p, o := groupedHTTPFixtureProfile(t, c)
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
					if used[i] || resource != x.URL || r.Method != x.Method || !groupedHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
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
					for key, value := range x.ResponseHeaders {
						w.Header().Set(key, value)
					}
					w.Header().Set("Content-Type", "application/json; charset=utf-8")
					if c.Provider == "jobconvo" || strings.HasPrefix(x.Body, "inploi ") {
						w.Header().Set("Content-Type", "text/html; charset=utf-8")
					}
					w.WriteHeader(x.Status)
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
			for _, u := range used {
				if !u {
					t.Fatal("original HTTP outcome omitted a request")
				}
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
			var expected []map[string]any
			if c.Provider == "jobconvo" {
				var urls []string
				if json.Unmarshal(c.Jobs, &urls) != nil {
					t.Fatal("original URL inventory")
				}
				for _, source := range urls {
					expected = append(expected, map[string]any{"url": source})
				}
			} else if json.Unmarshal(c.Jobs, &expected) != nil {
				t.Fatal("original rich inventory")
			}
			if len(out.Jobs) != len(expected) || out.Truncated != c.Truncated {
				t.Fatal("original inventory/truncation changed")
			}
			for i, fields := range expected {
				want := RichMonitorJob{URL: fields["url"].(string)}
				if c.Provider != "jobconvo" {
					var err error
					want, err = secondaryRichJob(fields)
					if err != nil {
						t.Fatal(err)
					}
					if locations, ok := fields["locations"].([]any); ok {
						for _, v := range locations {
							want.Locations = append(want.Locations, v.(string))
						}
					}
				}
				if !reflect.DeepEqual(jsonNormalizedRichJob(t, out.Jobs[i]), jsonNormalizedRichJob(t, want)) {
					t.Fatalf("canonical HTTP fields differ at %d", i)
				}
			}
		})
	}
}
