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
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type notionHTTPReference struct {
	Name, Board          string
	Metadata             json.RawMessage
	Public, Chunk, Query json.RawMessage
	URLs                 []string
	Error                bool
	Calls                []struct {
		URL     string
		Payload map[string]any
	}
}

func notionHTTPCases(t *testing.T) []notionHTTPReference {
	t.Helper()
	var cases []notionHTTPReference
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_notion_monitor.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 14 {
		t.Fatal("actual Python HTTP corpus missing", err)
	}
	return cases
}

func notionHTTPHandler(t *testing.T, c notionHTTPReference, reserved bool) http.HandlerFunc {
	t.Helper()
	seen := map[string]int{}
	return func(w http.ResponseWriter, r *http.Request) {
		endpoint := "https://" + r.Host + r.URL.Path
		var payload map[string]any
		if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&payload) != nil {
			t.Error("invalid anonymous Notion request")
			w.WriteHeader(400)
			return
		}
		seen[endpoint]++
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(endpoint, "getPublicPageData") {
			if r.Host != "www.notion.so" && (c.Name == "root-canonical-fallback" || c.Name == "explicit-no-fallback") {
				w.WriteHeader(500)
				fmt.Fprint(w, `{}`)
				return
			}
			if c.Name == "root-403" {
				w.WriteHeader(403)
				fmt.Fprint(w, `{}`)
				return
			}
			w.Write(c.Public)
			return
		}
		if strings.HasSuffix(endpoint, "loadPageChunk") {
			if reserved {
				w.Header().Set("TDM-Reservation", "1")
				w.Write(c.Chunk)
				return
			}
			if c.Name == "transient-chunk" && seen[endpoint] == 1 {
				w.WriteHeader(503)
				fmt.Fprint(w, `{}`)
				return
			}
			if c.Name == "explicit-home-fallback" && payload["page"].(map[string]any)["id"] == "22222222-2222-2222-2222-222222222222" {
				fmt.Fprint(w, `{"recordMap":{"block":{"22222222-2222-2222-2222-222222222222":{"value":{"type":"page","properties":{"title":[["No jobs"]]},"content":[]}}}}}`)
				return
			}
			w.Write(c.Chunk)
			return
		}
		if !strings.HasSuffix(endpoint, "queryCollection") {
			t.Error("unexpected endpoint", endpoint)
			w.WriteHeader(400)
			return
		}
		if c.Name == "late-collection-failure" {
			w.WriteHeader(503)
			fmt.Fprint(w, `{}`)
			return
		}
		w.Write(c.Query)
	}
}

func TestNotionActualPythonHTTPRequestsRetryFallbackAndInventories(t *testing.T) {
	for _, c := range notionHTTPCases(t) {
		t.Run(c.Name, func(t *testing.T) {
			step := 0
			handler := notionHTTPHandler(t, c, false)
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if step >= len(c.Calls) {
					t.Error("unexpected HTTP request")
					w.WriteHeader(400)
					return
				}
				expected := c.Calls[step]
				step++
				var body map[string]any
				raw, err := json.Marshal(expected.Payload)
				if err != nil {
					t.Fatal(err)
				}
				data := json.NewDecoder(r.Body)
				if data.Decode(&body) != nil || !reflect.DeepEqual(body, expected.Payload) || "https://"+r.Host+r.URL.Path != expected.URL {
					t.Error("request differs", body, expected)
				}
				r.Body = io.NopCloser(strings.NewReader(string(raw)))
				handler(w, r)
			}))
			waits := 0
			out, err := FetchNotionHTTP(context.Background(), client.client, queue.GreenhouseMonitorProfile{Provider: "notion", Profile: "notion.public-urls/v1", Endpoint: "https://fixture.notion.site/api/v3/getPublicPageData"}, map[string]string{"board_url": c.Board, "metadata": string(c.Metadata), "monitor_needs_browser": "0"}, func(context.Context, time.Duration) error { waits++; return nil })
			if (err != nil) != c.Error || step != len(c.Calls) || err != nil && len(out.Jobs) != 0 {
				t.Fatal(err, step, len(c.Calls), out)
			}
			if !c.Error {
				urls := []string{}
				for _, job := range out.Jobs {
					if !job.URLOnly {
						t.Fatal("Notion monitor became rich")
					}
					urls = append(urls, job.URL)
				}
				if !reflect.DeepEqual(urls, c.URLs) {
					t.Fatal(urls, c.URLs)
				}
			}
			if c.Name == "root-canonical-fallback" && waits != 2 || c.Name == "transient-chunk" && waits != 1 {
				t.Fatal("bounded retry differs", waits)
			}
		})
	}
}

func TestNotionLatePublisherReservationDiscardsInventory(t *testing.T) {
	c := notionHTTPCases(t)[0]
	client := verifiedClaimFixtureClient(t, notionHTTPHandler(t, c, true))
	out, err := FetchNotionHTTP(context.Background(), client.client, queue.GreenhouseMonitorProfile{Provider: "notion", Profile: "notion.public-urls/v1", Endpoint: "https://fixture.notion.site/api/v3/getPublicPageData"}, map[string]string{"board_url": c.Board, "metadata": string(c.Metadata), "monitor_needs_browser": "0"}, noSecondaryWait)
	var reservation *policy.Reservation
	if !errors.As(err, &reservation) || len(out.Jobs) != 0 {
		t.Fatal("late publisher reservation swallowed", err, out)
	}
}

func TestNotionTransportFailureDoesNotAuthorizeHTTP500RootFallback(t *testing.T) {
	requests := 0
	client := &http.Client{Transport: testRoundTripper(func(r *http.Request) (*http.Response, error) {
		requests++
		if r.URL.Host != "fixture.notion.site" {
			t.Fatal("transport failure authorized canonical-host fallback", r.URL.Host)
		}
		if requests == 1 {
			return &http.Response{StatusCode: 500, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{}`)), Request: r}, nil
		}
		return nil, errors.New("fixture transport failure")
	})}
	out, err := FetchNotionHTTP(context.Background(), client, queue.GreenhouseMonitorProfile{Provider: "notion", Profile: "notion.public-urls/v1", Endpoint: "https://fixture.notion.site/api/v3/getPublicPageData"}, map[string]string{"board_url": "https://fixture.notion.site/", "metadata": "{}", "monitor_needs_browser": "0"}, noSecondaryWait)
	if err == nil || requests != 3 || len(out.Jobs) != 0 {
		t.Fatal("last transport failure lost", requests, err, out)
	}
}
