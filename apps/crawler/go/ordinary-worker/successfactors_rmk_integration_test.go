package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
)

func TestRealSuccessFactorsRMKCompleteDuplicateFailureAndReservation(t *testing.T) {
	var corpus []struct {
		Name      string
		Responses []string
	}
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_successfactors_rmk.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil {
		t.Fatal("original fixture missing")
	}
	for _, proxy := range []bool{false, true} {
		for _, scraper := range []string{"skip", "json-ld"} {
			for _, mode := range []string{"complete", "repeated", "prefix-failure", "reserved-later"} {
				t.Run(fmt.Sprintf("proxy=%t/%s/%s", proxy, scraper, mode), func(t *testing.T) {
					name := "eleven"
					if mode == "repeated" {
						name = "repeated"
					}
					if mode == "prefix-failure" {
						name = "short-tail"
					}
					var responses []string
					for _, c := range corpus {
						if c.Name == name {
							responses = c.Responses
						}
					}
					if len(responses) != 3 {
						t.Fatal("original three-request fixture missing")
					}
					metadata := map[string]any{"preset": "successfactors", "variant": "rmk", "brand": "Fixture", "scraper_type": scraper, "proxy": proxy}
					if scraper != "skip" {
						metadata["scraper_config"] = map[string]any{"enrich": []string{"description"}}
					}
					md, _ := json.Marshal(metadata)
					f := privateRichPipelineFixtureURL(t, "rss", string(md), "https://example.com/Fixture/jobs")
					claim, circuits := claimFixture(t, f)
					ctx := context.Background()
					selected, e := runtimeClaimUsesProxy(ctx, f.a, claim)
					if e != nil || selected != proxy {
						t.Fatal("runtime transport differs", e)
					}
					calls := 0
					client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						i := calls
						calls++
						if i >= len(responses) {
							t.Error("extra request")
							w.WriteHeader(400)
							return
						}
						if i == 0 {
							if r.Method != "GET" || r.URL.Path != "/Fixture/jobs" {
								t.Error("bootstrap request differs")
							}
							http.SetCookie(w, &http.Cookie{Name: "rmk-fixture", Value: "session", Path: "/", Secure: true})
						} else {
							if r.Method != "POST" || r.URL.Path != "/services/recruiting/v1/jobs" || r.Header.Get("X-Csrf-Token") != "fixture-csrf" {
								t.Error("scoped API request differs")
							}
							cookie, e := r.Cookie("rmk-fixture")
							if e != nil || cookie.Value != "session" {
								t.Error("bootstrap cookie lost")
							}
							var body struct {
								PageNumber int `json:"pageNumber"`
							}
							b, _ := io.ReadAll(r.Body)
							if json.Unmarshal(b, &body) != nil || body.PageNumber != i-1 {
								t.Error("original page order differs")
							}
						}
						if mode == "reserved-later" && i == 2 {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(503)
							return
						}
						fmt.Fprint(w, responses[i])
					}))}
					if proxy {
						client = credentialedProxyFixture(t, client)
					}
					result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
					if e != nil || result == nil || !result.Settled {
						t.Fatal("RMK claim did not settle", e)
					}
					assertRichDeadlineAndLease(t, f, "rss")
					var missing, failures, count int
					var reserved bool
					if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
						t.Fatal(e)
					}
					if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &count); e != nil {
						t.Fatal(e)
					}
					wantCount, wantMissing, wantFailures := 0, 0, 0
					if mode == "complete" {
						wantCount, wantMissing = 11, 1
					}
					if mode == "repeated" {
						wantCount = 10
					}
					if mode == "prefix-failure" {
						wantFailures = 1
					}
					if count != wantCount || missing != wantMissing || failures != wantFailures || reserved != (mode == "reserved-later") || calls != 3 {
						t.Fatal("canonical inventory, absence or publisher effects changed", count, missing, failures, reserved, calls)
					}
					if wantCount > 0 {
						var title string
						var due bool
						if e := f.pg.QueryRow(ctx, "SELECT titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid ORDER BY source_url LIMIT 1", f.board, f.original).Scan(&title, &due); e != nil {
							t.Fatal(e)
						}
						if title != "Engineer 1" || due != (scraper != "skip") {
							t.Fatal("rich title or downstream detail assignment changed", title, due)
						}

					}
				})
			}
		}
	}
}
