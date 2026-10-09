package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"testing"
)

func TestRealFinalHTTPProvidersCanonicalFieldsAndTerminalSettlement(t *testing.T) {
	for _, c := range finalHTTPInventoryCases(t) {
		if c.Mode != "rich" && !(c.Provider == "paynet" && c.Mode == "duplicate") {
			continue
		}
		for _, mode := range []string{"complete", "invalid", "reserved", "gone"} {
			t.Run(c.Provider+"/"+c.Mode+"/"+mode, func(t *testing.T) {
				var md map[string]any
				if json.Unmarshal(c.Board.Metadata, &md) != nil {
					t.Fatal("original metadata unavailable")
				}
				md["scraper_type"] = "skip"
				metadata, _ := json.Marshal(md)
				f := privateRichPipelineFixtureURL(t, c.Provider, string(metadata), c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				used := make([]bool, len(c.Exchanges))
				var mu sync.Mutex
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "gone" {
						w.WriteHeader(404)
						return
					}
					if mode == "invalid" {
						fmt.Fprint(w, "malformed")
						return
					}
					body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
					resource := "https://" + r.Host + r.URL.RequestURI()
					mu.Lock()
					defer mu.Unlock()
					for i, x := range c.Exchanges {
						if !used[i] && resource == x.URL && r.Method == x.Method && finalHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
							used[i] = true
							w.Header().Set("Content-Type", "application/json; charset=utf-8")
							if c.Provider == "fenbi" {
								w.Header().Set("Content-Type", "text/html; charset=utf-8")
							}
							fmt.Fprint(w, x.Body)
							return
						}
					}
					t.Error("unmatched original pipeline exchange")
					w.WriteHeader(400)
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("real owned claim failed to settle", e, result)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var reserved bool
				var failures, gone int
				if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				wantFailure, wantGone := 0, 0
				if mode == "invalid" || mode == "gone" && c.Provider != "nowhiring" {
					wantFailure = 1
				}
				if mode == "gone" && c.Provider == "nowhiring" {
					wantGone = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailure || gone != wantGone {
					t.Fatal("terminal board state changed", reserved, failures, gone)
				}
				if mode != "complete" {
					var active bool
					var title string
					if result.Batches.Inserted != 0 {
						t.Fatal("failed inventory published prefix")
					}
					if e = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil || !active || title != "Original" {
						t.Fatal("failed inventory changed existing posting", e)
					}
					return
				}
				if result.Batches.Inserted != len(c.Jobs) {
					t.Fatal("complete inventory lost postings", result.Batches)
				}
				if c.Truncated {
					var active bool
					if e = f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); e != nil || !active {
						t.Fatal("truncated inventory delisted unseen posting", e)
					}
				}
				for _, expected := range c.Jobs {
					job, e := secondaryRichJob(expected)
					if e != nil || job.Title == nil {
						t.Fatal(e)
					}
					var count int
					if e = f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND p.source_identity=$3 AND p.titles[1]=$4 AND p.next_scrape_at IS NULL AND NOT d.r2_uploaded`, f.board, expected["url"], expected["source_identity"], *job.Title).Scan(&count); e != nil || count != 1 {
						t.Fatal("canonical identity/title/R2 intent/skip schedule changed", e, count)
					}
				}
			})
		}
	}
}
