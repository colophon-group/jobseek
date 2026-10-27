package worker

import (
	"context"
	"encoding/pem"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealAPIMonitorHTTP2SelectionAndCanonicalSettlement(t *testing.T) {
	for _, supportsHTTP2 := range []bool{true, false} {
		t.Run(fmt.Sprint("server-http2-", supportsHTTP2), func(t *testing.T) {
			f := privateRichPipelineFixture(t, "api_sniffer", `{"api_url":"https://example.com/api/jobs","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"scraper_type":"skip"}`)
			var http1Requests, http2Requests atomic.Int64
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/jobs" || r.Method != "GET" {
					t.Error("API request identity changed")
				}
				if r.ProtoMajor != 2 {
					http1Requests.Add(1)
					if supportsHTTP2 {
						w.WriteHeader(http.StatusForbidden)
						return
					}
				} else {
					http2Requests.Add(1)
				}
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"jobs":[{"id":"one","title":"Engineer"},{"id":"two","title":"Scientist"}]}`)
			}))
			server.EnableHTTP2 = supportsHTTP2
			server.StartTLS()
			t.Cleanup(server.Close)

			client := func(http2 bool) *VerifiedHTTP {
				bundle := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw})
				verified, err := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle, EnableHTTP2: http2})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(verified.CloseIdleConnections)
				transport := verified.client.Transport.(*directTransport)
				transport.inner.TLSClientConfig.ServerName = "example.com"
				transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
					return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
				}
				transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
					if address != "8.8.8.8:443" {
						t.Error("API destination was not validated and pinned")
					}
					return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
				}
				return verified
			}
			http1, http2 := client(false), client(true)
			claim, circuits := claimFixture(t, f)
			selected := directMonitorHTTP(claim.Descriptor().Config["crawler_type"], http1, http2)
			result, err := RunGreenhouseClaim(context.Background(), f.a, claim, selected, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("API claim did not settle", err)
			}
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(context.Background(), `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted); err != nil {
				t.Fatal(err)
			}
			wantHTTP1, wantHTTP2 := int64(0), int64(1)
			if !supportsHTTP2 {
				wantHTTP1, wantHTTP2 = 1, 0
			}
			if http1Requests.Load() != wantHTTP1 || http2Requests.Load() != wantHTTP2 || inserted != 2 || missing != 1 || failures != 0 || reserved {
				t.Fatal("HTTP/2 API inventory did not publish and settle", http1Requests.Load(), http2Requests.Load(), inserted, missing, failures, reserved)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
		})
	}
}
