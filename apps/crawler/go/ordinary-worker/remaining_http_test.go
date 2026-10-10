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
	"sort"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type remainingHTTPInventoryCase struct {
	Provider, Mode string
	Board          struct {
		URL      string `json:"board_url"`
		Metadata json.RawMessage
	}
	Exchanges []struct {
		Method, URL, Body string
		RequestBody       string `json:"request_body"`
		Headers           map[string]string
		Status            int
		ContentType       string `json:"content_type"`
	}
	Jobs             json.RawMessage
	Truncated, Error bool
}

func remainingHTTPInventoryCases(t *testing.T) []remainingHTTPInventoryCase {
	t.Helper()
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_remaining_http_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []remainingHTTPInventoryCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 29 {
		t.Fatal("original inventory corpus unavailable")
	}
	if directory := os.Getenv("JOBSEEK_REMAINING_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
		for _, slug := range []string{"city-of-montreux-careers", "fei-careers", "kraft-heinz-careers-ru", "mindlance-job-board", "ntt-data-it-contract", "sucden-russia-hh"} {
			raw, e := os.ReadFile(directory + "/native999-remaining-http-" + slug + "-public-capture1-2026-10-10.json")
			if e != nil {
				t.Fatal("private capture missing", slug)
			}
			var c remainingHTTPInventoryCase
			if json.Unmarshal(raw, &c) != nil {
				t.Fatal("private capture invalid", slug)
			}
			cases = append(cases, c)
		}
	}
	return cases
}

func remainingHTTPFixtureClient(t *testing.T, c remainingHTTPInventoryCase, mode string) (*VerifiedHTTP, []bool) {
	t.Helper()
	used := make([]bool, len(c.Exchanges))
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if mode == "reserved" {
			w.Header().Set("TDM-Reservation", "1")
			w.WriteHeader(503)
			return
		}
		if mode == "failed" {
			w.WriteHeader(400)
			return
		}
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		resource := "https://" + r.Host + r.URL.RequestURI()
		parsed, _ := url.Parse(resource)
		for i, x := range c.Exchanges {
			want, _ := url.Parse(x.URL)
			if used[i] || r.Method != x.Method || parsed.Scheme != want.Scheme || parsed.Host != want.Host || parsed.Path != want.Path || !reflect.DeepEqual(parsed.Query(), want.Query()) || !finalHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
				continue
			}
			for _, key := range []string{"accept", "content-type", "authorization", "portalid", "a", "compid", "token", "referer"} {
				if key == "accept" && x.Headers[key] == "*/*" {
					continue
				}
				if value := x.Headers[key]; value != "" && r.Header.Get(key) != value {
					t.Error("original request header changed", key)
				}
			}
			if c.Provider == "headhunter" && r.Header.Get("User-Agent") != x.Headers["user-agent"] {
				t.Error("original HeadHunter identity changed")
			}
			used[i] = true
			w.Header().Set("Content-Type", x.ContentType)
			w.WriteHeader(x.Status)
			fmt.Fprint(w, x.Body)
			return
		}
		t.Error("unmatched original HTTP exchange", c.Provider, c.Mode)
		w.WriteHeader(400)
	}))
	if c.Provider == "headhunter" {
		client = credentialedProxyFixture(t, client)
	}
	return client, used
}

func TestRemainingHTTPOriginalInventoriesThroughVerifiedTransport(t *testing.T) {
	for _, c := range remainingHTTPInventoryCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			o, e := api.RemainingHTTPOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
			if e != nil {
				t.Fatal(e)
			}
			config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
			client, used := remainingHTTPFixtureClient(t, c, "")
			out, e := FetchRemainingHTTPProviders(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			if c.Error {
				if e == nil || len(out.Jobs) != 0 {
					t.Fatal("original failed inventory gained write authority")
				}
				return
			}
			if e != nil {
				t.Fatal("original inventory failed", e)
			}
			if out.Truncated != c.Truncated {
				t.Fatal("truncation changed")
			}
			gotURLs := []string{}
			for _, job := range out.Jobs {
				gotURLs = append(gotURLs, job.URL)
			}
			sort.Strings(gotURLs)
			wantURLs := []string{}
			if c.Provider == "headhunter" {
				var jobs []map[string]any
				json.Unmarshal(c.Jobs, &jobs)
				for _, job := range jobs {
					wantURLs = append(wantURLs, job["url"].(string))
				}
			} else {
				json.Unmarshal(c.Jobs, &wantURLs)
			}
			sort.Strings(wantURLs)
			if !reflect.DeepEqual(gotURLs, wantURLs) {
				t.Fatal("original full inventory changed")
			}
			for _, consumed := range used {
				if !consumed {
					t.Fatal("original exchange skipped")
				}
			}
		})
	}
}

func TestRemainingHTTPPublisherPrecedenceAndCancellation(t *testing.T) {
	for _, c := range remainingHTTPInventoryCases(t) {
		if c.Mode != "complete" {
			continue
		}
		for _, mode := range []string{"reserved", "cancelled"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				o, e := api.RemainingHTTPOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
				if e != nil {
					t.Fatal(e)
				}
				config := map[string]string{"crawler_type": c.Provider, "board_url": c.Board.URL, "metadata": string(c.Board.Metadata), "monitor_needs_browser": "0"}
				p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
				client, _ := remainingHTTPFixtureClient(t, c, "reserved")
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if mode == "cancelled" {
					cancel()
				}
				waits := 0
				out, e := FetchRemainingHTTPProviders(ctx, client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				if e == nil || len(out.Jobs) != 0 || waits != 0 || mode == "reserved" && (out.Response == nil || !out.Response.reserved) {
					t.Fatal("publisher/cancellation authority changed")
				}
			})
		}
	}
}

func TestJohdiSamePathOfferIdentitySurvivesInventoryFiltering(t *testing.T) {
	board := "https://employer.example/careers"
	for _, fragment := range []string{"#/offer/23/job", "#/offer/23/translated-title/"} {
		out, e := NormalizeRichInventory(context.Background(), board, []RichMonitorJob{{URL: board + fragment, URLOnly: true}}, false)
		if e != nil || len(out.Jobs) != 1 || out.Jobs[0].URL != board+fragment || len(out.DropReasons) != 0 {
			t.Fatal("valid Johdi identity filtered", fragment, e)
		}
	}
	for _, fragment := range []string{"#0", "#/job/first", "#/offer/0/job", "#/offer/023/job", "#/offer/23", "#/offer/23/job/extra", "#/offer/word/job"} {
		if classifyJobURL(board+fragment, board) != "board_homepage" {
			t.Fatal("invalid homepage fragment accepted", fragment)
		}
	}
}
