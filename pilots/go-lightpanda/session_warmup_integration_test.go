//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestLightpandaWarmupRetainsOnlyItsOwnSessionForAPIAndRSS(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	for _, kind := range []string{"api", "rss"} {
		for _, mode := range []string{"success", "blocked", "reserved"} {
			t.Run(kind+"/"+mode, func(t *testing.T) {
				var warmups, listings atomic.Int64
				certificate, ca := dayforceOriginTLS(t)
				origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/warmup" {
						warmups.Add(1)
						if _, err := r.Cookie("warmup-session"); err == nil {
							t.Error("new task reused a previous browser session")
						}
						w.Header().Set("Content-Type", "text/html")
						if mode == "blocked" {
							w.WriteHeader(503)
						} else if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
						}
						http.SetCookie(w, &http.Cookie{Name: "warmup-session", Value: "private-cookie", Path: "/", Secure: true, HttpOnly: true})
						fmt.Fprint(w, `<html><body><a href="/jobs/decoy">Not inventory</a><script>document.cookie='warmup-js=held; path=/; secure';</script></body></html>`)
						return
					}
					if r.URL.Path != "/careers" && r.URL.Path != "/api" && r.URL.Path != "/feed" {
						http.NotFound(w, r)
						return
					}
					listings.Add(1)
					for name, value := range map[string]string{"warmup-session": "private-cookie", "warmup-js": "held"} {
						c, err := r.Cookie(name)
						if err != nil || c.Value != value {
							t.Error("warmup cookies did not reach the real navigation")
							w.WriteHeader(403)
							return
						}
					}
					switch r.URL.Path {
					case "/careers":
						w.Header().Set("Content-Type", "text/html")
						fmt.Fprint(w, `<html><body><script>fetch('/api')</script></body></html>`)
					case "/api":
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprint(w, `{"jobs":[{"url":"https://jobs.dayforcehcm.com/jobs/actual","title":"Engineer"}]}`)
					case "/feed":
						w.Header().Set("Content-Type", "application/rss+xml")
						fmt.Fprint(w, `<rss><channel><item><title>Engineer</title></item></channel></rss>`)
					}
				}))
				address := "127.0.0.2"
				if runtime.GOOS == "darwin" {
					address = "127.0.0.1"
				}
				listener, err := net.Listen("tcp4", address+":0")
				if err != nil {
					t.Fatal(err)
				}
				origin.Listener = listener
				origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
				origin.StartTLS()
				defer origin.Close()
				proxy := dayforceConnectProxyAt(t, origin, address)
				runner := func(ctx context.Context, c Config, task Task) (Result, error) {
					c.Binary, c.EgressPolicy = binary, defaultEgressPolicy()
					return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
				}
				execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runner}
				repeats := 1
				if mode == "success" {
					repeats = 2 // A second whole task must start without either cookie.
				}
				for attempt := 0; attempt < repeats; attempt++ {
					ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
					if kind == "api" {
						metadata := `{"browser":true,"warmup_url":"https://jobs.dayforcehcm.com/warmup","api_url":"https://jobs.dayforcehcm.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"settle":0.1}`
						response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://jobs.dayforcehcm.com/careers", Metadata: json.RawMessage(metadata), TimeoutMS: 25000})
						var inventory api.Inventory
						want := map[string]string{"success": "success", "blocked": "failed", "reserved": "publisher_reserved"}[mode]
						if err != nil || response.Outcome != want || mode != "success" && len(response.Inventory) != 0 {
							t.Fatal("warmup API outcome or cleanup differs", response.Outcome, err)
						}
						if mode == "success" && (json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated || len(inventory.Jobs) != 1 || inventory.Jobs[0].URL != "https://jobs.dayforcehcm.com/jobs/actual") {
							t.Fatal("warmup became inventory or real inventory was lost")
						}
					} else {
						called := false
						err := execution.executeFeed(ctx, feed.Request{Protocol: feed.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), FeedURL: "https://jobs.dayforcehcm.com/feed", WarmupURL: "https://jobs.dayforcehcm.com/warmup", Start: 1, Increment: 1, MaxPages: 1, Wait: "domcontentloaded", NavigationTimeoutMS: 10000, TimeoutMS: 25000}, func(ctx context.Context, fetch feedPageFetch) (Result, error) {
							called = true
							page, err := fetch(ctx, "https://jobs.dayforcehcm.com/feed")
							if err != nil || page.Status != 200 || !strings.Contains(string(page.ResponseBody), "<item>") {
								return Result{}, feed.ErrProtocol
							}
							return Result{feedSessionSettled: true}, nil
						})
						var reservation *policy.Reservation
						if mode == "success" && (err != nil || !called) || mode != "success" && (err == nil || called) || mode == "reserved" && !errors.As(err, &reservation) {
							t.Fatal("warmup feed published or lost its policy failure", err)
						}
					}
					cancel()
				}
				if warmups.Load() != int64(repeats) || mode != "success" && listings.Load() != 0 {
					t.Fatal("warmup retried or failed warmup reached inventory")
				}
			})
		}
	}
}
