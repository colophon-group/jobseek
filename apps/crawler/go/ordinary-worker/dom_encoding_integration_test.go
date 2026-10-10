package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/text/encoding/japanese"
	"golang.org/x/text/transform"
)

func encodedJapaneseHTML(t *testing.T, html string) string {
	t.Helper()
	encoded, _, err := transform.String(japanese.EUCJP.NewEncoder(), html)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func TestRealJapaneseEncodingMonitorAndDetailSettlement(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		for _, encoding := range []string{"euc_jp", "EUC-JP"} {
			for _, mode := range []string{"complete", "reserved", "empty"} {
				t.Run(fmt.Sprintf("monitor/proxy=%t/%s/%s", proxy, encoding, mode), func(t *testing.T) {
					md, _ := json.Marshal(map[string]any{"encoding": encoding, "url_filter": "/jobs/", "scraper_type": "json-ld", "proxy": proxy})
					f := privateRichPipelineFixture(t, "dom", string(md))
					ctx := context.Background()
					claim, circuits := claimFixture(t, f)
					calls := 0
					client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						calls++
						if r.Method != "GET" || r.URL.Path != "/careers" {
							t.Error("encoded monitor left canonical source")
						}
						w.Header().Set("Content-Type", "text/html; charset=utf-8")
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(503)
						}
						html := `<h1>技術職の募集</h1><a href="/jobs/123">勤務地：東京</a>`
						if mode == "empty" {
							html = `<h1>技術職の募集</h1>`
						}
						fmt.Fprint(w, encodedJapaneseHTML(t, html))
					}))
					if proxy {
						client = credentialedProxyFixture(t, client)
					}
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
					wantCalls := 1
					if err != nil || result == nil || !result.Settled || calls != wantCalls {
						t.Fatal("encoded monitor did not settle", err)
					}
					var missing, inserted, failures, emptyChecks int
					var optedOut bool
					if err := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.empty_check_count,b.tdm_reserved,b.consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &emptyChecks, &optedOut, &failures, &inserted); err != nil {
						t.Fatal(err)
					}
					want := 1
					if mode != "complete" {
						want = 0
					}
					if missing != want || inserted != want || failures != 0 || optedOut != (mode == "reserved") || int(f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val()) != want {
						t.Fatal("encoded monitor changed absence, policy or detail schedule", missing, inserted, failures, optedOut)
					}
					wantEmptyChecks := 0
					if mode == "empty" {
						wantEmptyChecks = 1
					}
					if emptyChecks != wantEmptyChecks {
						t.Fatal("encoded inventory changed original empty-check count", emptyChecks)
					}
					if mode == "complete" {
						var source string
						var titles []string
						if err := f.pg.QueryRow(ctx, "SELECT source_url,titles FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&source, &titles); err != nil || source != "https://example.com/jobs/123" || len(titles) != 0 {
							t.Fatal("URL-only inventory acquired rich authority or changed source", err)
						}
					}
					assertRichDeadlineAndLease(t, f, "dom")
				})
			}
			t.Run(fmt.Sprintf("detail/proxy=%t/%s", proxy, encoding), func(t *testing.T) {
				md, _ := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": map[string]any{"encoding": encoding, "steps": []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "h2", "text": "Role", "offset": 1, "field": "description", "html": true}}}})
				f, a, claim := independentDetailOwnedFixture(t, proxyDetailFixtureMetadata(t, string(md), proxy), "")
				ctx := context.Background()
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					if r.Method != "GET" || r.URL.Path != "/job/"+f.original {
						t.Error("encoded detail left canonical source")
					}
					w.Header().Set("Content-Type", "text/html; charset=utf-8")
					fmt.Fprint(w, encodedJapaneseHTML(t, `<h1>技術職の募集</h1><h2>Role</h2><p>勤務地：東京</p>`))
				})
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				if err != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 {
					t.Fatal("encoded detail did not settle", err)
				}
				var title, html string
				var due time.Time
				if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],d.html,p.next_scrape_at FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &html, &due); err != nil || title != "技術職の募集" || !strings.Contains(html, "勤務地：東京") {
					t.Fatal("canonical Japanese text changed", err)
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("encoded detail deadline or lease changed", err)
				}
			})
		}
	}
}
