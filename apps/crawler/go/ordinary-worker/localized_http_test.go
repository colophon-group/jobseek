package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func localizedFixtureConfig(t *testing.T, provider, board string, metadata json.RawMessage) (queue.GreenhouseMonitorProfile, map[string]string) {
	t.Helper()
	var md map[string]any
	if json.Unmarshal(metadata, &md) != nil {
		t.Fatal("fixture metadata")
	}
	md["scraper_type"] = "skip"
	if provider == "talemetry" {
		md["scraper_type"] = "json-ld"
	}
	raw, _ := json.Marshal(md)
	config := map[string]string{"board_slug": "fixture", "board_url": board, "crawler_type": provider, "company_id": "22222222-2222-4222-8222-222222222222", "metadata": string(raw), "check_interval_minutes": "30", "scrape_interval_hours": "24", "domain": "fixture", "throttle_key": "fixture", "monitor_needs_browser": "0", "scraper_needs_browser": "0"}
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil {
		t.Fatal(e)
	}
	return p, config
}

func TestLocalizedTalemetryOriginalVerifiedTransport(t *testing.T) {
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_talemetry.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name      string
		Board     string `json:"board_url"`
		Metadata  json.RawMessage
		Responses []string
		Requests  []struct {
			URL     string
			Headers map[string]string
		}
		URLs  []string
		Error bool
	}
	if json.Unmarshal(body, &cases) != nil {
		t.Fatal("original Talemetry corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, config := localizedFixtureConfig(t, "talemetry", c.Board, c.Metadata)
			used := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if used >= len(c.Requests) {
					t.Error("extra inventory request")
					w.WriteHeader(400)
					return
				}
				want := c.Requests[used]
				source := c.Responses[used]
				used++
				if "https://"+r.Host+r.URL.String() != want.URL || r.Method != "GET" {
					t.Error("request changed")
				}
				for k, v := range want.Headers {
					if r.Header.Get(k) != v {
						t.Error("header changed", k)
					}
				}
				fmt.Fprint(w, source)
			}))
			out, e := FetchLocalizedHTTPProviders(context.Background(), client, p, config, func(context.Context, time.Duration) error { return nil })
			if (e != nil) != c.Error {
				t.Fatalf("error differs %v", e)
			}
			if used != len(c.Requests) {
				t.Fatal("original request count changed")
			}
			if c.Error {
				if len(out.Jobs) != 0 {
					t.Fatal("failed prefix escaped")
				}
				return
			}
			urls := []string{}
			for _, j := range out.Jobs {
				if !j.URLOnly {
					t.Fatal("URL inventory became rich")
				}
				urls = append(urls, j.URL)
			}
			if !reflect.DeepEqual(urls, c.URLs) {
				t.Fatal("complete inventory differs")
			}
		})
	}
}

func TestLocalizedProspectiveOriginalVerifiedTransport(t *testing.T) {
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_prospective.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Board string
		Metadata    json.RawMessage
		Requests    []struct {
			URL, Method, Body string
			Headers           map[string]string
		}
		Responses []struct {
			Body    string
			Status  int
			Headers map[string]string
		}
		Jobs  []map[string]any
		Error bool
	}
	if json.Unmarshal(body, &cases) != nil {
		t.Fatal("original Prospective corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, config := localizedFixtureConfig(t, "prospective", c.Board, c.Metadata)
			var mu sync.Mutex
			used := make([]bool, len(c.Requests))
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				body, _ := io.ReadAll(r.Body)
				raw := "https://" + r.Host + r.URL.String()
				found := -1
				for i, x := range c.Requests {
					if !used[i] && x.URL == raw && x.Method == r.Method && x.Body == string(body) {
						found = i
						break
					}
				}
				if found < 0 {
					t.Error("extra or changed request")
					w.WriteHeader(400)
					return
				}
				used[found] = true
				for k, v := range c.Requests[found].Headers {
					if r.Header.Get(k) != v {
						t.Error("header changed", k)
					}
				}
				response := c.Responses[found]
				for k, v := range response.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(response.Status)
				fmt.Fprint(w, response.Body)
			}))
			out, e := FetchLocalizedHTTPProviders(context.Background(), client, p, config, func(context.Context, time.Duration) error { return nil })
			if (e != nil) != c.Error {
				t.Fatalf("error differs %v", e)
			}
			for _, u := range used {
				if !u {
					t.Fatal("original request absent")
				}
			}
			if c.Error {
				if len(out.Jobs) != 0 {
					t.Fatal("failed localized prefix escaped")
				}
				return
			}
			if len(out.Jobs) != len(c.Jobs) {
				t.Fatal("localized count differs")
			}
			for i, j := range out.Jobs {
				want := c.Jobs[i]
				if j.URL != want["url"] || j.Title == nil || *j.Title != want["title"] || j.Description == nil || *j.Description != want["description"] || j.Language != want["language"] {
					t.Fatal("selected localized content differs")
				}
				actual, _ := json.Marshal(j.Locations)
				expected, _ := json.Marshal(want["locations"])
				if string(actual) != string(expected) {
					t.Fatal("selected location differs")
				}
				localizations, _ := want["localizations"].(map[string]any)
				locales := []string{}
				titles := []string{}
				for locale := range localizations {
					locales = append(locales, locale)
				}
				sort.Strings(locales)
				for _, locale := range locales {
					m := localizations[locale].(map[string]any)
					if title, ok := m["title"].(string); ok {
						titles = append(titles, title)
					}
				}
				if !reflect.DeepEqual(j.LocalizationLocales, locales) || !reflect.DeepEqual(j.LocalizedTitles, titles) {
					t.Fatal("original locale/title aliases differ")
				}
			}
		})
	}
}

func TestLocalizedPublisherSignalsPrecedeStatusAndRetry(t *testing.T) {
	for _, provider := range []string{"talemetry", "prospective", "kipt"} {
		for _, inBody := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/body%t", provider, inBody), func(t *testing.T) {
				board := "https://careers.example.com/search/jobs"
				md := json.RawMessage(`{}`)
				if provider == "prospective" {
					var cases []struct {
						Board    string
						Metadata json.RawMessage
					}
					b, _ := os.ReadFile("../api-sniffer-monitor/testdata/python_prospective.json")
					json.Unmarshal(b, &cases)
					board, md = cases[0].Board, cases[0].Metadata
				}
				if provider == "kipt" {
					board = "https://www.kipt.kharkov.ua/ua/vacancy.html"
				}
				p, config := localizedFixtureConfig(t, provider, board, md)
				requests := 0
				client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					w.Header().Set("Content-Type", "text/html")
					if !inBody {
						w.Header().Set("TDM-Reservation", "1")
					}
					w.WriteHeader(404)
					if inBody {
						fmt.Fprint(w, `<html><head><meta name="tdm-reservation" content="1"></head></html>`)
					}
				}))
				out, e := FetchLocalizedHTTPProviders(context.Background(), client, p, config, func(context.Context, time.Duration) error { t.Fatal("reserved response retried"); return nil })
				var reserved *policy.Reservation
				if !errors.As(e, &reserved) || requests != 1 || len(out.Jobs) != 0 || out.Response == nil || !out.Response.reserved {
					t.Fatalf("publisher evidence lost %v", e)
				}
			})
		}
	}
}
