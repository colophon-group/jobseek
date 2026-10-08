package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
)

func TestRealTalentBrewInventoriesScheduleConfiguredDetailsAndPreserveFailures(t *testing.T) {
	for _, mode := range []string{"complete", "late-missing", "late-reserved", "bad-count", "missing-first", "cross-redirect"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "talentbrew", `{"scraper_type":"json-ld","ajax":false}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if mode == "missing-first" || calls > 1 && mode == "late-missing" {
					w.WriteHeader(404)
					return
				}
				if mode == "cross-redirect" {
					w.Header().Set("Location", "https://other.example/secret")
					w.WriteHeader(302)
					return
				}
				if calls > 1 && mode == "late-reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				total := 2
				if mode == "bad-count" {
					total = 3
				}
				fmt.Fprintf(w, `<div id="search-results" data-total-job-results="%d" data-total-pages="2" data-records-per-page="1"></div><div id="search-results-list"><a href="/job/new%d">Engineer</a></div>`, total, calls)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("TalentBrew failed settlement", err, result)
			}
			assertRichDeadlineAndLease(t, f, "talentbrew")
			var inserted, failures, gone, missing int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				var titled, described, due int
				if err := f.pg.QueryRow(ctx, `SELECT count(*) FILTER(WHERE cardinality(titles)>0),count(*) FILTER(WHERE EXISTS(SELECT 1 FROM descriptions d WHERE d.posting_id=p.id)),count(*) FILTER(WHERE next_scrape_at IS NOT NULL) FROM job_posting p WHERE board_id=$1::uuid AND id<>$2::uuid`, f.board, f.original).Scan(&titled, &described, &due); err != nil {
					t.Fatal(err)
				}
				if inserted != 2 || titled != 0 || described != 0 || due != 2 || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 2 || failures != 0 || reserved || gone != 0 {
					t.Fatal("listing lost separate detail authority", inserted, titled, described, due, failures, gone, reserved)
				}
				return
			}
			wantFailures := 1
			if mode == "late-reserved" || mode == "missing-first" {
				wantFailures = 0
			}
			if inserted != 0 || failures != wantFailures || reserved != (mode == "late-reserved") || gone != 0 || missing != 0 {
				t.Fatal("failed/reserved listing finalized partial inventory", inserted, failures, gone, missing, reserved)
			}
		})
	}
}
