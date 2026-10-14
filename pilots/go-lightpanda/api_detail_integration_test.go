//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestLightpandaSingleDetailCaptureIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	for _, denied := range []bool{false, true} {
		t.Run(fmt.Sprint(denied), func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/detail":
					http.SetCookie(w, &http.Cookie{Name: "session", Value: "private-session", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					fmt.Fprint(w, `<!doctype html><html><body>Role<script>fetch('/api/detail',{method:'POST',headers:{'X-Csrf-Token':'private-fresh'}})</script></body></html>`)
				case "/api/detail":
					cookie, e := r.Cookie("session")
					if e != nil || cookie.Value != "private-session" || r.Header.Get("X-Csrf-Token") != "private-fresh" {
						http.Error(w, "missing session", 403)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					if denied {
						w.Header().Set("TDM-Reservation", "1")
					}
					fmt.Fprint(w, `{"data":{"title":"Engineer","body":"<p>A complete original description of more than fifty characters.</p>","location":"Zurich"}}`)
				default:
					http.NotFound(w, r)
				}
			}))
			address := "127.0.0.2:0"
			if runtime.GOOS == "darwin" {
				address = "127.0.0.1:0"
			}
			listener, e := net.Listen("tcp4", address)
			if e != nil {
				t.Fatal(e)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxyAt(t, origin, strings.TrimSuffix(address, ":0"))
			runner := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: strings.TrimSuffix(address, ":0")}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runner}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			response, e := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Operation: "detail", BoardURL: "https://jobs.dayforcehcm.com/detail", Metadata: json.RawMessage(`{"fields":{"title":"title","description":"body","locations":"location"},"settle":0.25}`), TimeoutMS: 25000})
			if e != nil || !response.Valid() {
				t.Fatal("physical detail protocol failed", e)
			}
			if denied {
				if response.Outcome != "publisher_reserved" || len(response.Detail) != 0 {
					t.Fatal("reservation leaked detail")
				}
				return
			}
			var fields map[string]any
			if response.Outcome != "success" || json.Unmarshal(response.Detail, &fields) != nil || fields["title"] != "Engineer" || fields["description"] != "<p>A complete original description of more than fifty characters.</p>" || strings.Contains(string(response.Detail), "private-") {
				t.Fatal("physical detail capture or cleanup failed", response.Outcome)
			}
		})
	}
}
