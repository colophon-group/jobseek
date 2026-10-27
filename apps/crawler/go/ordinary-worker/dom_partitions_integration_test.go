package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMPartitionsPublishOnlyCompleteInventories(t *testing.T) {
	for _, mode := range []string{"complete", "count-changed", "blocked-tail", "reserved-tail"} {
		t.Run(mode, func(t *testing.T) {
			md := `{"url_filter":"/jobs/","pagination":{"param_name":"page","max_pages":4,"transient_403":true,"partition_selector":"a.facet","partition_count_regex":"\\((\\d+) vacancies","partition_validate_total":true,"partition_stateless":true},"scraper_type":"skip"}`
			f := privateRichPipelineFixture(t, "dom", md, queue.Simple)
			ctx := context.Background()
			claim, e := f.a.Claim(ctx, queue.Simple)
			if e != nil || claim == nil {
				t.Fatal("partition monitor claim missing")
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.Header().Set("Set-Cookie", "sticky=root; Path=/")
				if r.URL.Query().Get("facet") == "" {
					fmt.Fprint(w, `<title>Jobs (3 vacancies)</title><a class="facet" href="?facet=a">All</a>`)
					return
				}
				if r.Header.Get("Cookie") != "" {
					t.Error("partition inherited root cookies")
				}
				if r.URL.Query().Get("page") == "2" {
					if mode == "blocked-tail" {
						w.WriteHeader(503)
						fmt.Fprint(w, "unavailable")
						return
					}
					if mode == "reserved-tail" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(404)
						return
					}
					if mode != "count-changed" {
						fmt.Fprintf(w, `<title>Jobs (3 vacancies)</title><a href="/jobs/%s/three">Three</a>`, f.company)
						return
					}
				}
				fmt.Fprintf(w, `<title>Jobs (3 vacancies)</title><a href="/jobs/%s/one">One</a><a href="/jobs/%s/two">Two</a>`, f.company, f.company)
			}))
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("partition attempt failed to settle")
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Simple)
			var inserted, missing, failures int
			var reserved bool
			if e = f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid`, f.board, f.original).Scan(&inserted); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, `SELECT missing_count FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&missing); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, `SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid`, f.board).Scan(&failures, &reserved); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if inserted != 3 || missing != 1 || failures != 0 || reserved {
					t.Fatal("complete partition publication changed")
				}
				return
			}
			wantFailures := 1
			wantStatus := "failed"
			if mode == "reserved-tail" {
				wantFailures = 0
				wantStatus = "publisher_reserved"
			}
			if inserted != 0 || missing != 0 || failures != wantFailures || reserved != (mode == "reserved-tail") || result.Cycle.Status != wantStatus {
				t.Fatal("incomplete/reserved partition changed canonical inventory")
			}
		})
	}
}
