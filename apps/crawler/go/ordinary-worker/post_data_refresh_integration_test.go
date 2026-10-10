package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"
)

func TestRealPOSTRefreshCookiesPolicyAndAtomicInventory(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		for _, mode := range []string{"complete", "missing-nonce", "reserved-bootstrap", "reserved-later", "invalid-later"} {
			t.Run(fmt.Sprintf("proxy=%t/%s", proxy, mode), func(t *testing.T) {
				metadata := map[string]any{"api_url": "https://example.com/api", "method": "POST", "json_path": "jobs", "total_path": "total", "url_field": "url", "fields": map[string]any{"title": "title", "description": "body"}, "post_data": "action=jobs&nonce=old&page=1", "request_headers": map[string]any{"Content-Type": "application/x-www-form-urlencoded", "X-API-Only": "fixture"}, "post_data_refresh": map[string]any{"fields": map[string]any{"nonce": "nonce=([a-z]+)"}}, "pagination": map[string]any{"param_name": "page", "style": "page", "start_value": 1, "max_pages": 2, "location": "body"}, "scraper_type": "skip", "proxy": proxy}
				md, _ := json.Marshal(metadata)
				f := privateRichPipelineFixtureURL(t, "api_sniffer", string(md), "https://example.com/careers")
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if r.URL.Path == "/careers" {
						if calls != 1 || r.Method != "GET" || r.Header.Get("X-API-Only") != "" || len(r.Cookies()) != 0 {
							t.Error("bootstrap leaked API-only headers or foreign operation cookies")
							w.WriteHeader(400)
							return
						}
						if mode == "reserved-bootstrap" {
							w.WriteHeader(503)
							fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
							return
						}
						http.SetCookie(w, &http.Cookie{Name: "operation", Value: "fresh", Path: "/", Secure: true})
						w.WriteHeader(202)
						if mode == "missing-nonce" {
							fmt.Fprint(w, "no token")
						} else {
							fmt.Fprint(w, "nonce=fresh")
						}
						return
					}
					cookie, e := r.Cookie("operation")
					raw, _ := io.ReadAll(r.Body)
					if r.URL.Path != "/api" || r.Method != "POST" || r.Header.Get("X-API-Only") != "fixture" || e != nil || cookie.Value != "fresh" {
						t.Error("public bootstrap cookie or scoped API request changed")
						w.WriteHeader(400)
						return
					}
					page := 1
					if calls > 2 {
						page = 2
					}
					want := fmt.Sprintf("action=jobs&nonce=fresh&page=%d", page)
					if string(raw) != want {
						t.Error("ordered refreshed request or page changed")
						w.WriteHeader(400)
						return
					}
					if page == 2 && mode == "reserved-later" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if page == 2 && mode == "invalid-later" {
						fmt.Fprint(w, "invalid JSON")
						return
					}
					fmt.Fprintf(w, `{"total":2,"jobs":[{"url":"https://example.com/job/%s/%d","title":"Engineer","body":"<p>Build systems.</p>"}]}`, f.company, page)
				}))
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("refresh claim did not settle", e)
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer")
				var missing, failures, count, details int
				var reserved bool
				if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &count, &details); e != nil {
					t.Fatal(e)
				}
				wantCount, wantMissing, wantFailure, wantCalls := 0, 0, 0, 3
				switch mode {
				case "complete":
					wantCount, wantMissing = 2, 1
				case "missing-nonce":
					wantFailure, wantCalls = 1, 1
				case "reserved-bootstrap":
					wantCalls = 1
				case "invalid-later":
					wantFailure, wantCalls = 1, 5
				}
				if count != wantCount || missing != wantMissing || failures != wantFailure || calls != wantCalls || details != 0 || reserved != (mode == "reserved-bootstrap" || mode == "reserved-later") {
					t.Fatal("bootstrap failure/publisher/absence or scheduling effects changed", count, missing, failures, calls, details, reserved)
				}
			})
		}
	}
}
