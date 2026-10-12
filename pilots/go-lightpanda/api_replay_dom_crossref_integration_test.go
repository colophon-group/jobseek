//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestLightpandaAPIDOMCrossrefSessionIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	for _, denied := range []bool{false, true} {
		t.Run(fmt.Sprint(denied), func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/dom-careers":
					http.SetCookie(w, &http.Cookie{Name: "session", Value: "private-session", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					fmt.Fprint(w, `<!doctype html><html><body><a href="/ux/ats/requisition/3829">Engineer</a><script>fetch('/api/dom',{method:'POST',headers:{'X-Csrf-Token':'private-fresh'}})</script></body></html>`)
				case "/api/dom":
					cookie, e := r.Cookie("session")
					if e != nil || cookie.Value != "private-session" || r.Method != "POST" || r.Header.Get("X-Csrf-Token") != "private-fresh" {
						http.Error(w, "private session missing", 403)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					id := "3829"
					if r.URL.Query().Get("page") == "2" {
						id = "3821"
						if denied {
							w.Header().Set("TDM-Reservation", "1")
						}
					}
					fmt.Fprintf(w, `{"total":2,"jobs":[{"requisitionId":%s,"title":"Engineer","description":"<p>Public role</p>"}]}`, id)
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
			metadata := `{"browser":true,"api_url":"https://jobs.dayforcehcm.com/api/dom","method":"POST","json_path":"jobs","fields":{"title":"title","description":"description"},"request_headers":{"X-Csrf-Token":"stale"},"pagination":{"param_name":"page","location":"query","style":"page","start_value":1,"increment":1},"settle":0.25}`
			runner := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: strings.TrimSuffix(address, ":0")}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runner}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			response, e := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://jobs.dayforcehcm.com/dom-careers", Metadata: json.RawMessage(metadata), TimeoutMS: 25000})
			if e != nil {
				t.Fatal("protocol failed")
			}
			if denied {
				if response.Outcome != "publisher_reserved" || len(response.Inventory) > 0 {
					t.Fatal("later publisher denial published a prefix")
				}
				return
			}
			var inventory api.Inventory
			if response.Outcome != "success" || json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated || len(inventory.Jobs) != 2 {
				t.Fatal("complete physical cross-reference unavailable", response.Outcome)
			}
			if inventory.Jobs[0].URL != "https://jobs.dayforcehcm.com/ux/ats/requisition/3829" || inventory.Jobs[1].URL != "https://jobs.dayforcehcm.com/ux/ats/requisition/3821" {
				t.Fatal("original DOM-derived identities changed")
			}
		})
	}
}
