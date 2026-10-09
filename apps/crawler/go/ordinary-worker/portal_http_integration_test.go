package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"testing"
)

func TestRealPortalHTTPProvidersCanonicalContentSchedulesAndTerminalStates(t *testing.T) {
	for _, c := range portalInventoryCases(t) {
		if c.Name != "populated" && !(c.Provider == "infoniqa" && c.Name == "initial-jobs") && !(c.Provider == "pageup" && c.Name == "late-snapshot-change") {
			continue
		}
		modes := []string{"complete", "reserved", "failed", "gone"}
		if c.Name == "late-snapshot-change" {
			modes = []string{"prefix-failure"}
		}
		for _, mode := range modes {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				var metadata map[string]any
				if json.Unmarshal(c.Board.Metadata, &metadata) != nil {
					t.Fatal("fixture metadata")
				}
				metadata["scraper_type"] = "skip"
				if c.Provider == "infoniqa" {
					metadata["scraper_type"] = "dom"
					metadata["scraper_config"] = map[string]any{"steps": []any{map[string]any{"tag": "h1", "field": "title"}}}
				}
				if c.Provider == "pageup" {
					metadata["scraper_type"] = "dom"
					metadata["scraper_config"] = map[string]any{"steps": []any{map[string]any{"tag": "p", "field": "description"}}, "enrich": []string{"description"}}
				}
				body, _ := json.Marshal(metadata)
				f := privateRichPipelineFixtureURL(t, c.Provider, string(body), c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				index := 0
				var mu sync.Mutex
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					mu.Lock()
					defer mu.Unlock()
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "failed" {
						w.WriteHeader(400)
						return
					}
					if mode == "gone" {
						w.WriteHeader(404)
						return
					}
					if index >= len(c.Exchanges) {
						t.Error("unexpected terminal fixture request")
						w.WriteHeader(400)
						return
					}
					x := c.Exchanges[index]
					index++
					for key, value := range x.Response.Headers {
						w.Header().Set(key, value)
					}
					fmt.Fprint(w, x.Response.Body)
				}))
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("owned portal claim did not settle", err, result)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var reserved bool
				var failures, gone int
				if err = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); err != nil {
					t.Fatal(err)
				}
				wantFailure, wantGone := 0, 0
				if mode == "failed" || mode == "prefix-failure" || mode == "gone" && c.Provider != "pageup" && c.Provider != "keka" {
					wantFailure = 1
				}
				if mode == "gone" && (c.Provider == "pageup" || c.Provider == "keka") {
					wantGone = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailure || gone != wantGone {
					t.Fatal("original terminal state differs", reserved, failures, gone)
				}
				if mode != "complete" {
					var active bool
					var title string
					if err = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil || !active || title != "Original" {
						t.Fatal("failed complete inventory changed absent original", err, active, title)
					}
					wantInserted := 0
					if mode == "prefix-failure" {
						wantInserted = 500
					}
					if result.Batches.Inserted != wantInserted {
						t.Fatal("validated page prefix changed", result.Batches.Inserted, wantInserted)
					}
					if mode == "prefix-failure" {
						var count int
						if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND is_active AND next_scrape_at IS NOT NULL", f.board, f.original).Scan(&count); err != nil || count != 500 {
							t.Fatal("committed page prefix lost detail schedule", err, count)
						}
					}
					return
				}
				if result.Batches.Inserted != 1 {
					t.Fatal("original posting omitted", result.Batches)
				}
				var count int
				if c.Provider == "pageup" || c.Provider == "infoniqa" {
					if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url=$2 AND next_scrape_at IS NOT NULL", f.board, c.Expected[0]["url"]).Scan(&count); err != nil || count != 1 {
						t.Fatal("URL/hybrid detail schedule changed", err, count)
					}
				} else {
					wantDescription := c.CanonicalHTML[0]
					if !strings.HasPrefix(wantDescription, "<") {
						t.Fatal("original description missing")
					}
					if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND p.next_scrape_at IS NULL AND d.html=$3 AND NOT d.r2_uploaded", f.board, c.Expected[0]["url"], wantDescription).Scan(&count); err != nil || count != 1 {
						var html, source string
						var scheduled, uploaded bool
						_ = f.pg.QueryRow(ctx, "SELECT p.source_url, p.next_scrape_at IS NOT NULL, d.html, d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&source, &scheduled, &html, &uploaded)
						t.Fatal("canonical rich HTML/R2 intent or skip schedule differs", err, count, source, scheduled, uploaded, html, wantDescription)
					}
				}
			})
		}
	}
}
