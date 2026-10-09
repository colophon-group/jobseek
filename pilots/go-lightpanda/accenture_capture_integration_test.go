//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestLightpandaAccentureCapturedRequestIntegration(t *testing.T) {
	binary := integrationBinary(t)
	for _, mode := range []string{"complete", "publisher", "later-failure"} {
		t.Run(mode, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t, "www.accenture.com")
			var mu sync.Mutex
			requests := []int{}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "GET" {
					http.SetCookie(w, &http.Cookie{Name: "accenture-session", Value: "private-accenture-cookie", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					io.WriteString(w, `<!doctype html><html><body>Careers<script>fetch('/api/accenture/jobsearch/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({startIndex:0,maxResultSize:10,opaque:'page-owned'})})</script></body></html>`)
					return
				}
				var request struct {
					Start  int    `json:"startIndex"`
					Size   int    `json:"maxResultSize"`
					Opaque string `json:"opaque"`
				}
				cookie, err := r.Cookie("accenture-session")
				if err != nil || cookie.Value != "private-accenture-cookie" || r.URL.Path != "/api/accenture/jobsearch/result" || json.NewDecoder(r.Body).Decode(&request) != nil || request.Opaque != "page-owned" || request.Size != 10 && request.Size != 500 {
					http.Error(w, "public captured session lost", 400)
					return
				}
				mu.Lock()
				requests = append(requests, request.Start)
				mu.Unlock()
				if request.Size == 500 && request.Start == 500 {
					if mode == "publisher" {
						w.Header().Set("TDM-Reservation", "1")
						fmt.Fprint(w, "unparseable reserved response")
						return
					}
					if mode == "later-failure" {
						w.WriteHeader(404)
						return
					}
				}
				w.Header().Set("Content-Type", "application/json")
				count := request.Size
				if request.Start == 500 {
					count = 1
				}
				items := []map[string]any{}
				for i := 0; i < count; i++ {
					items = append(items, map[string]any{"jobDetailUrl": fmt.Sprintf("/fr-fr/careers/jobdetails?id=%d", request.Start+i), "title": "Engineer", "jobCityState": "Paris", "postedDate": "2026-10-09"})
				}
				json.NewEncoder(w).Encode(map[string]any{"totalHits": 501, "data": items})
			}))
			listener, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxy(t, origin, "www.accenture.com")
			fixture := newServiceTLSFixture(t)
			runner := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, runner)
			if err != nil {
				t.Fatal(err)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
			defer stop()
			client := dayforceClientFixture(t, fixture, address)
			ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
			defer cancel()
			held, err := client.Reserve(ctx)
			if err != nil {
				t.Fatal(err)
			}
			response, err := held.APIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://www.accenture.com/fr-fr/careers/jobsearch", Metadata: json.RawMessage(`{"country":"France","language":"fr","site":"fr-fr","endpoint":"jobsearch/result","scraper_type":"skip"}`), Provider: "accenture", TimeoutMS: 30000})
			want := "success"
			if mode == "publisher" {
				want = "publisher_reserved"
			}
			if mode == "later-failure" {
				want = "failed"
			}
			if err != nil || response.Outcome != want {
				t.Fatal("physical captured service outcome changed", err, response.Outcome)
			}
			if want == "success" {
				var inventory api.Inventory
				if json.Unmarshal(response.Inventory, &inventory) != nil || len(inventory.Jobs) != 501 || inventory.Truncated || strings.Contains(string(response.Inventory), "private-accenture-cookie") || strings.Contains(string(response.Inventory), "page-owned") {
					t.Fatal("captured inventory or privacy changed")
				}
			} else if len(response.Inventory) != 0 {
				t.Fatal("failed captured pagination published prefix")
			}
			mu.Lock()
			defer mu.Unlock()
			if fmt.Sprint(requests) != "[0 0 500]" {
				t.Fatal("original capture and pagination changed", requests)
			}
		})
	}
}
