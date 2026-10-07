//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Reuse the isolated HTTPS fixture transport. No production URL, proxy or
// destination-policy exemption is introduced by this test.
func TestLightpandaAPIReplaySessionIntegration(t *testing.T) {
	binary := integrationBinary(t)
	for _, denied := range []bool{false, true} {
		name := "private-cookie-and-pagination"
		if denied {
			name = "later-publisher-denial"
		}
		t.Run(name, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			var mu sync.Mutex
			pages := []string{}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/replay-careers":
					http.SetCookie(w, &http.Cookie{Name: "replay-session", Value: "private-cookie", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					_, _ = io.WriteString(w, `<!doctype html><html><body><script>fetch('/api/replay',{method:'POST',headers:{'X-Csrf-Token':'private-fresh-csrf'}})</script>Careers</body></html>`)
				case "/api/replay":
					cookie, err := r.Cookie("replay-session")
					if r.Method != "POST" || r.Header.Get("X-Csrf-Token") != "private-fresh-csrf" || err != nil || cookie.Value != "private-cookie" {
						http.Error(w, "private session missing", 403)
						return
					}
					page := r.URL.Query().Get("page")
					mu.Lock()
					pages = append(pages, page)
					mu.Unlock()
					w.Header().Set("Content-Type", "application/json")
					if denied && page == "2" {
						w.Header().Set("Tdm-Reservation", "1")
					}
					id := "1"
					if page == "2" {
						id = "2"
					}
					_, _ = io.WriteString(w, `{"total":2,"jobs":[{"id":"`+id+`","title":"Engineer"}]}`)
				default:
					http.NotFound(w, r)
				}
			}))
			listener, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxy(t, origin)
			metadata := strings.ReplaceAll(replayControllerMetadata, "https://example.com", "https://jobs.dayforcehcm.com")
			metadata = strings.Replace(metadata, `https://jobs.dayforcehcm.com/api"`, `https://jobs.dayforcehcm.com/api/replay"`, 1)
			metadata = strings.Replace(metadata, `"settle":0`, `"settle":0.25`, 1)
			// The actual page must replace this stale configured header.
			metadata = strings.TrimSuffix(metadata, "}") + `,"request_headers":{"X-Csrf-Token":"stale-csrf"}}`
			var task Task
			var inventory api.Inventory
			task, err = newAPIReplayTask("https://jobs.dayforcehcm.com/replay-careers", metadata, func(ctx context.Context, fetch api.Fetch) error {
				var err error
				inventory, err = api.DiscoverBrowserReplay(ctx, task.APIReplay.options, fetch, replayControllerJoin, false)
				return err
			})
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			result, err := runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 15 * time.Second}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			if denied {
				var reservation *policy.Reservation
				if !errors.As(err, &reservation) || len(inventory.Jobs) != 0 {
					t.Fatal("later denial persisted partial inventory", err)
				}
			} else if err != nil || len(inventory.Jobs) != 2 || inventory.Truncated || !result.apiReplaySessionSettled || result.HTML != "" {
				t.Fatal("real replay capture/cookie/pagination/cleanup failed", err, len(inventory.Jobs))
			}
			mu.Lock()
			defer mu.Unlock()
			if len(pages) != 2 || pages[0] != "" || pages[1] != "2" {
				t.Fatal("first capture was lost or replayed", pages)
			}
		})
	}
}
