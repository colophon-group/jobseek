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

// The real pinned binary, TLS client/service, browser cookies, fetch/CDP bridge
// and cleanup run through the established network-none origin/proxy fixture.
func TestLightpandaNativeBrowserProviderSessionIntegration(t *testing.T) {
	binary := integrationBinary(t)
	for _, mode := range []string{"darwin-complete", "darwin-prefix", "darwin-publisher", "darwin-gone", "byte-global", "byte-experienced", "byte-campus"} {
		t.Run(mode, func(t *testing.T) {
			provider, board := "darwinbox", "https://airtel.darwinbox.in/ms/candidate/careers"
			if strings.HasPrefix(mode, "byte") {
				provider = "bytedance"
				board = "https://joinbytedance.com/search"
				if mode == "byte-experienced" {
					board = "https://jobs.bytedance.com/experienced/position"
				}
				if mode == "byte-campus" {
					board = "https://jobs.bytedance.com/campus/position"
				}
			}
			hosts := []string{"airtel.darwinbox.in", "jobs.bytedance.com", "joinbytedance.com"}
			certificate, ca := dayforceOriginTLS(t, hosts...)
			var mu sync.Mutex
			requests := 0
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Access-Control-Allow-Origin", "https://joinbytedance.com")
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "accept, content-type, website-path, portal-platform, portal-channel, x-tt-env")
				if r.Method == "OPTIONS" {
					w.WriteHeader(204)
					return
				}
				if !strings.HasPrefix(r.URL.Path, "/api/") && r.URL.Path != "/ms/candidateapi/job/alljobs" {
					http.SetCookie(w, &http.Cookie{Name: "native-session", Value: "private-native-cookie", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					io.WriteString(w, `<!doctype html><html><body>Careers</body></html>`)
					return
				}
				mu.Lock()
				requests++
				mu.Unlock()
				w.Header().Set("Content-Type", "application/json")
				if mode != "byte-global" {
					cookie, e := r.Cookie("native-session")
					if e != nil || cookie.Value != "private-native-cookie" {
						http.Error(w, "session missing", 403)
						return
					}
				}
				if provider == "darwinbox" {
					if r.Method != "POST" || r.Header.Get("Authorization") != "undefined" || r.Header.Get("X-Requested-With") != "XMLHttpRequest" {
						http.Error(w, "original headers missing", 400)
						return
					}
					var body struct {
						Company string `json:"companyId"`
						Page    int    `json:"page"`
						Limit   int    `json:"limit"`
					}
					if json.NewDecoder(r.Body).Decode(&body) != nil || body.Company != "main" || body.Limit != 100 {
						http.Error(w, "body mismatch", 400)
						return
					}
					if mode == "darwin-gone" {
						w.WriteHeader(410)
						return
					}
					if body.Page == 2 && mode == "darwin-publisher" {
						w.Header().Set("Tdm-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if body.Page == 2 && mode == "darwin-prefix" {
						io.WriteString(w, `{"status":"failed"}`)
						return
					}
					n, total := 1, 1
					if mode == "darwin-prefix" || mode == "darwin-publisher" {
						n, total = 100, 101
					}
					rows := []any{}
					for i := 0; i < n; i++ {
						rows = append(rows, map[string]any{"id": fmt.Sprint((body.Page-1)*100 + i + 1), "title": "Engineer", "jd": "<p>Build</p>", "locations": "Zurich"})
					}
					json.NewEncoder(w).Encode(map[string]any{"status": "success", "job_counts": total, "data": rows})
					return
				}
				if r.Header.Get("Portal-Platform") != "pc" {
					http.Error(w, "portal header missing", 400)
					return
				}
				if r.URL.Path == "/api/v1/config/job/filters/2" {
					json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"job_type_list": []any{map[string]string{"id": "engineering"}, map[string]string{"id": "sales"}}, "job_type_count_map": map[string]int{"engineering": 1, "sales": 1}}})
					return
				}
				var body struct {
					Offset     int      `json:"offset"`
					Categories []string `json:"job_category_id_list"`
					Limit      int      `json:"limit"`
				}
				if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&body) != nil || body.Limit != 1000 {
					http.Error(w, "search body mismatch", 400)
					return
				}
				id := "1"
				if len(body.Categories) > 0 {
					id = body.Categories[0]
				}
				payload := map[string]any{"code": 0, "data": map[string]any{"count": 1, "job_post_list": []any{map[string]any{"id": id, "title": "Engineer", "description": "Build", "city_info": map[string]string{"en_name": "Zurich"}}}}}
				if mode == "byte-global" {
					payload["padding"] = strings.Repeat("x", 3<<20)
				}
				json.NewEncoder(w).Encode(payload)
			}))
			listener, e := net.Listen("tcp4", "127.0.0.2:0")
			if e != nil {
				t.Fatal(e)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxy(t, origin, hosts...)
			fixture := newServiceTLSFixture(t)
			runner := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution, e := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, runner)
			if e != nil {
				t.Fatal(e)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
			defer stop()
			client := dayforceClientFixture(t, fixture, address)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			held, e := client.Reserve(ctx)
			if e != nil {
				t.Fatal(e)
			}
			response, e := held.APIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: board, Metadata: json.RawMessage(`{"scraper_type":"skip"}`), Provider: provider, TimeoutMS: 15000})
			if e != nil {
				t.Fatal("real native session", e)
			}
			want := "success"
			if mode == "darwin-prefix" {
				want = "partial"
			}
			if mode == "darwin-publisher" {
				want = "publisher_reserved"
			}
			if mode == "darwin-gone" {
				want = "provider_gone"
			}
			if response.Outcome != want {
				t.Fatal("real native outcome", response.Outcome, want)
			}
			if want == "success" || want == "partial" {
				var inv api.Inventory
				if json.Unmarshal(response.Inventory, &inv) != nil {
					t.Fatal("inventory invalid")
				}
				count := 1
				if mode == "darwin-prefix" {
					count = 100
				}
				if mode == "byte-experienced" {
					count = 2
				}
				if len(inv.Jobs) != count || inv.Truncated || strings.Contains(string(response.Inventory), "private-native-cookie") {
					t.Fatal("inventory/count/privacy", len(inv.Jobs))
				}
			}
			mu.Lock()
			defer mu.Unlock()
			expected := 1
			if mode == "darwin-prefix" || mode == "darwin-publisher" {
				expected = 2
			}
			if mode == "byte-experienced" {
				expected = 3
			}
			if requests != expected {
				t.Fatal("original page/category request count", requests, expected)
			}
		})
	}
}
