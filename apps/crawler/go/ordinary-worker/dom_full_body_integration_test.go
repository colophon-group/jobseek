package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealDOMMyJobShopFullBodyAndJobPostingVerification(t *testing.T) {
	for _, mode := range []string{"complete", "missing-jobposting", "oversize", "root-policy", "detail-policy", "detail-failure"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"my_job_shop":true,"link_selector":"a.job","require_jsonld_jobposting":true,"scraper_type":"json-ld","transport_attempts":1}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			rootCalls, detailCalls := 0, 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/careers" {
					rootCalls++
					if mode == "root-policy" {
						w.Header().Set("TDM-Reservation", "1")
					}
					size := 600_000
					if mode == "oversize" {
						size = 2<<20 + 1
					}
					fmt.Fprint(w, "<html><body>"+strings.Repeat(" ", size))
					fmt.Fprintf(w, `<a class="job" href="/jobs/%s">Engineer</a></body></html>`, f.company)
					return
				}
				detailCalls++
				if r.URL.Path != "/jobs/"+f.company {
					t.Error("unbound detail verification")
				}
				if mode == "detail-policy" {
					w.Header().Set("TDM-Reservation", "1")
				}
				if mode == "detail-failure" {
					w.WriteHeader(503)
					return
				}
				if mode == "missing-jobposting" {
					fmt.Fprint(w, "<p>Closed offer</p>")
					return
				}
				fmt.Fprint(w, `<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>`)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || result == nil || !result.Settled || rootCalls != 1 {
				t.Fatal("full-body claim did not settle", err, rootCalls)
			}
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if inserted != 1 || missing != 1 || failures != 0 || reserved || detailCalls != 1 {
					t.Fatal("complete trailing offer was lost", inserted, missing, failures, detailCalls)
				}
			} else if inserted != 0 || missing != 0 {
				t.Fatal("unverified listing changed canonical jobs", inserted, missing)
			}
			policy := mode == "root-policy" || mode == "detail-policy"
			wantFailures := 0
			if mode == "oversize" || mode == "detail-failure" {
				wantFailures = 1
			}
			if reserved != policy || failures != wantFailures {
				t.Fatal("publisher policy or failed inventory outcome changed", reserved, failures)
			}
			if mode == "root-policy" && detailCalls != 0 || mode == "oversize" && detailCalls != 0 {
				t.Fatal("rejected root fetched details")
			}
			assertRichDeadlineAndLease(t, f, "dom")
		})
	}
}
