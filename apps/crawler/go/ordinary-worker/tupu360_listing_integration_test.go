package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
)

func TestRealTupu360CompleteInventoryAndLateFailureAuthority(t *testing.T) {
	for _, mode := range []string{"complete", "changed-total", "malformed", "reserved", "late-404"} {
		t.Run(mode, func(t *testing.T) {
			md := `{"tupu360_listing":true,"scraper_type":"tupu360","scraper_config":{"enrich":["description"],"listing_enrichment":true}}`
			f := privateRichPipelineFixtureURL(t, "dom", md, "https://fixture.tupu360.com/fixture/position/index")
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "fixture.tupu360.com" || r.URL.Path != "/fixture/position/index" || r.URL.Query().Get("max") != "100" {
					t.Error("request changed source authority")
				}
				if r.URL.Query().Get("offset") == "0" {
					fmt.Fprint(w, tupuTestPage(2, 1))
					return
				}
				switch mode {
				case "changed-total":
					fmt.Fprint(w, tupuTestPage(3, 2))
				case "malformed":
					fmt.Fprint(w, "layout drift")
				case "reserved":
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
				case "late-404":
					w.WriteHeader(404)
				default:
					fmt.Fprint(w, tupuTestPage(2, 2))
				}
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled || calls != 2 {
				t.Fatal("complete/failure cycle did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "dom")
			var reserved, active bool
			var failures, empty, gone, inserted int
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,empty_check_count,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &empty, &gone); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
				t.Fatal(err)
			}
			if mode != "complete" {
				if inserted != 0 || result.Batches.Inserted != 0 || !active || empty != 0 || gone != 0 || reserved != (mode == "reserved") || mode != "reserved" && failures != 1 {
					t.Fatal("incomplete inventory gained write/absence authority")
				}
				return
			}
			if inserted != 2 || result.Batches.Inserted != 2 || failures != 0 || reserved {
				t.Fatal("complete inventory did not persist")
			}
			rows, err := f.pg.Query(ctx, "SELECT id::text,source_url,titles[1],locales,next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original)
			if err != nil {
				t.Fatal(err)
			}
			defer rows.Close()
			for rows.Next() {
				var id, source, title string
				var locales []string
				var due bool
				if err := rows.Scan(&id, &source, &title, &locales, &due); err != nil {
					t.Fatal(err)
				}
				score, e := f.r.ZScore(ctx, "ft_scrapes_simple:fixture.tupu360.com", id).Result()
				if title != "工程师" || len(locales) != 1 || locales[0] != "zh" || !due || e != nil || score != 0 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("rich card fields or independent detail intent lost")
				}
			}
			if rows.Err() != nil {
				t.Fatal(rows.Err())
			}
		})
	}
}
