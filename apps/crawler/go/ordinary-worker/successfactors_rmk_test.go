package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func TestSuccessFactorsRMKOriginalPublicWorkerTransport(t *testing.T) {
	dir := os.Getenv("JOBSEEK_RMK_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires private original public RSS captures")
	}
	for _, slug := range []string{"zhengda-group-careers-my-cpf", "zhengda-group-careers-vn-cpf", "zhengda-group-careers-th-cpf"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(dir, "native1005-rss-group-"+slug+"-original-public-capture2-2026-10-10.json"))
			if err != nil {
				t.Fatal("capture unavailable")
			}
			var c struct {
				Status     string
				ObservedAt string `json:"observed_at_utc"`
				Board      map[string]json.RawMessage
				Jobs       []api.Job
				Truncated  bool
				Exchanges  []lastHTTPExchange
			}
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.UseNumber()
			if decoder.Decode(&c) != nil || c.Status != "complete" {
				t.Fatal("original complete capture unavailable")
			}
			config := map[string]string{}
			for key, value := range c.Board {
				if key == "metadata" {
					config[key] = string(value)
				} else {
					var s string
					if json.Unmarshal(value, &s) == nil {
						config[key] = s
					}
				}
			}
			p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if err != nil {
				t.Fatal("canonical config rejected", err)
			}
			observedAt, clockErr := time.Parse(time.RFC3339Nano, c.ObservedAt)
			if clockErr != nil {
				t.Fatal("original session clock missing")
			}
			clockShift := time.Since(observedAt)
			at, cookieRequests := 0, 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if at >= len(c.Exchanges) {
					t.Error("request exceeded original capture")
					w.WriteHeader(400)
					return
				}
				x := c.Exchanges[at]
				at++
				body, _ := io.ReadAll(r.Body)
				if r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL || string(body) != x.RequestBody {
					t.Error("original request changed")
					w.WriteHeader(400)
					return
				}
				for _, key := range []string{"content-type", "referer", "x-csrf-token"} {
					if want, ok := x.Headers[key]; ok && r.Header.Get(key) != want {
						t.Error("original explicit request header changed", key)
					}
				}
				if want := x.Headers["cookie"]; want != "" {
					cookieRequests++
					if !reflect.DeepEqual(lastHTTPCookiePairs(r.Header.Get("Cookie")), lastHTTPCookiePairs(want)) {
						t.Error("original session cookies changed")
					}
				}
				for k, v := range x.ResponseHeaders {
					if !strings.EqualFold(k, "Content-Length") && !strings.EqualFold(k, "Set-Cookie") {
						w.Header().Set(k, v)
					}
				}
				for _, cookie := range x.SetCookies {
					parts := strings.Split(cookie, ";")
					for i := 1; i < len(parts); i++ {
						key, value, ok := strings.Cut(strings.TrimSpace(parts[i]), "=")
						if ok && strings.EqualFold(key, "expires") {
							if expiry, e := http.ParseTime(value); e == nil {
								parts[i] = " Expires=" + expiry.Add(clockShift).UTC().Format(http.TimeFormat)
							}
						}
					}
					w.Header().Add("Set-Cookie", strings.Join(parts, ";"))
				}
				w.WriteHeader(x.Status)
				fmt.Fprint(w, x.Body)
			}))
			found, err := FetchSuccessFactorsRMKHTTP(context.Background(), client, p, config, func(context.Context, time.Duration) error { return nil })
			jobs := []api.Job{}
			for _, j := range found.Jobs {
				var title any
				if j.Title != nil {
					title = *j.Title
				}
				jobs = append(jobs, api.Job{URL: j.URL, SourceIdentity: j.SourceIdentity, Title: title, Description: j.Description, EmploymentType: j.EmploymentType, JobLocationType: j.JobLocationType, DatePosted: j.DatePosted, Locations: j.Locations, Metadata: j.Metadata, Extras: j.Extras})
				if j.Description == nil {
					jobs[len(jobs)-1].Description = nil
				}
			}
			for i := range c.Jobs {
				if c.Jobs[i].Metadata == nil {
					c.Jobs[i].Metadata = map[string]any{}
				}
				if c.Jobs[i].Extras == nil {
					c.Jobs[i].Extras = map[string]any{}
				}
			}
			sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
			sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i].URL < c.Jobs[j].URL })
			if err != nil || at != len(c.Exchanges) || found.Truncated != c.Truncated || !reflect.DeepEqual(jobs, c.Jobs) {
				t.Fatal("original worker requests, session or complete fields differ", err, len(jobs), len(c.Jobs))
			}
			t.Log("all original worker fields and session requests match", len(jobs), "cookie requests", cookieRequests)
		})
	}
}

func TestSuccessFactorsRMKErrorStatusPublisherPolicyAndRetries(t *testing.T) {
	for _, mode := range []string{"header-403", "meta-503", "direct-retry", "proxy-retry"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"preset": "successfactors", "variant": "rmk", "brand": "Fixture", "scraper_type": "skip", "proxy": mode == "proxy-retry"}
			raw, _ := json.Marshal(md)
			p, config := localizedFixtureConfig(t, "rss", "https://example.com/Fixture/jobs", raw)
			calls, waits := 0, 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if mode == "header-403" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(403)
					return
				}
				w.Header().Set("Content-Type", "text/html")
				w.WriteHeader(503)
				if mode == "meta-503" {
					fmt.Fprint(w, `<html><meta name="tdm-reservation" content="1"></html>`)
				}
			}))
			out, err := FetchSuccessFactorsRMKHTTP(context.Background(), client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
			wantCalls := 3
			if mode == "proxy-retry" {
				wantCalls = 6
			}
			if mode == "header-403" || mode == "meta-503" {
				wantCalls = 1
			}
			if err == nil || calls != wantCalls || waits != wantCalls-1 || len(out.Jobs) != 0 {
				t.Fatal("publisher stop or original retry bound changed", calls, waits, err)
			}
			if wantCalls == 1 && (out.Response == nil || !out.Response.reserved) {
				t.Fatal("error-status opt-out lost")
			}
		})
	}
}
