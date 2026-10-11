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
	"os"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestLightpandaRotatingAPISessionIntegration(t *testing.T) {
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		if binary == "" {
			t.Skip("requires official Lightpanda1.0.0 binary")
		}
		if verifyFileSHA256(binary, "955440053a84754dd64c62f970449a56a2b350cdf43ea5f2e809a73047b8173d") != nil {
			t.Fatal("official macOS asset differs")
		}
	} else {
		binary = integrationBinary(t)
	}
	for _, denied := range []bool{false, true} {
		t.Run(fmt.Sprint(denied), func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			var navigations, stored, later atomic.Int32
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/live-careers":
					n := navigations.Add(1)
					w.Header().Set("Content-Type", "text/html")
					fmt.Fprintf(w, `<!doctype html><html><body>Careers<script>fetch('/token/live%d/jobs')</script></body></html>`, n)
				case "/token/stored/jobs":
					stored.Add(1)
					w.WriteHeader(404)
				case "/token/live1/jobs", "/token/live2/jobs":
					w.Header().Set("Content-Type", "application/json")
					id := "1"
					if r.URL.Query().Get("page") == "2" {
						later.Add(1)
						id = "2"
						if r.URL.Path != "/token/live2/jobs" {
							t.Error("pagination used initial stale live token")
						}
						if denied {
							w.Header().Set("TDM-Reservation", "1")
						}
					}
					fmt.Fprintf(w, `{"total":2,"jobs":[{"id":"%s","title":"Engineer"}]}`, id)
				default:
					http.NotFound(w, r)
				}
			}))
			address := "127.0.0.2:0"
			if runtime.GOOS == "darwin" {
				address = "127.0.0.1:0"
			}
			listener, err := net.Listen("tcp4", address)
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxyAt(t, origin, strings.TrimSuffix(address, ":0"))
			metadata := `{"browser":true,"api_url":"https://jobs.dayforcehcm.com/token/stored/jobs","api_url_match":"jobs.dayforcehcm.com/token/*/jobs","method":"GET","json_path":"jobs","url_template":"https://jobs.dayforcehcm.com/jobs/{id}","fields":{"title":"title"},"pagination":{"param_name":"page","start_value":1,"increment":1,"style":"page"},"settle":0.25}`
			runner := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				result, err := runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: strings.TrimSuffix(address, ":0")}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
				if err != nil {
					t.Logf("owned fixture runner: %v", err)
				}
				return result, err
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runner}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://jobs.dayforcehcm.com/live-careers", Metadata: json.RawMessage(metadata), TimeoutMS: 25000})
			if err != nil {
				t.Fatal(err)
			}
			if denied {
				if response.Outcome != "publisher_reserved" || len(response.Inventory) != 0 {
					t.Fatal("later denial published prefix", response.Outcome)
				}
			} else {
				var inventory api.Inventory
				if response.Outcome != "success" || json.Unmarshal(response.Inventory, &inventory) != nil || len(inventory.Jobs) != 2 || inventory.Truncated {
					t.Fatal("rotating inventory failed", response.Outcome)
				}
			}
			if navigations.Load() != 2 || stored.Load() != 1 || later.Load() != 1 {
				t.Fatal("original retry sequence differs", navigations.Load(), stored.Load(), later.Load())
			}
			t.Log("pinned Lightpanda1.0.0 rotating path, pagination, policy and child cleanup proved")
		})
	}
}
