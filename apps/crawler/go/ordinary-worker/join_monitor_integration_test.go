package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestRealOwnedJoinMonitorCompletePaginationAndPolicyBoundaries(t *testing.T) {
	for _, mode := range []string{"success", "later_failure", "header", "meta", "gone404", "gone410"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "join", `{"scraper_type":"json-ld"}`)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.Host != "join.com" {
					t.Error("Join changed its direct request contract")
				}
				if strings.HasPrefix(mode, "gone") {
					status := 404
					if mode == "gone410" {
						status = 410
					}
					w.WriteHeader(status)
					return
				}
				if r.URL.Query().Get("page") == "2" {
					if mode == "later_failure" {
						w.WriteHeader(404)
						return
					}
					if mode == "header" || mode == "meta" {
						http.Redirect(w, r, "/policy-page", 302)
						return
					}
				}
				if r.URL.Path == "/policy-page" {
					if mode == "header" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "actual-page-policy")
						w.WriteHeader(404)
					} else {
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="actual-page-policy">`)
					}
					return
				}
				page := r.URL.Query().Get("page")
				if page == "" {
					page = "1"
				}
				fmt.Fprintf(w, `<script id="__NEXT_DATA__">{"props":{"pageProps":{"initialState":{"jobs":{"items":[{"idParam":"%s-engineer"}],"pagination":{"pageCount":3}}}}}}</script>`, page)
			}))
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatalf("Join did not settle its owned URL cycle: %+v %v", result, err)
			}
			var active, reserved bool
			var missing, failures, postings int
			var evidence *string
			var due time.Time
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text,next_check_at,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence, &due, &postings); err != nil {
				t.Fatal(err)
			}
			if mode == "success" {
				if result.Batches.Inserted != 3 || active || missing != 4 || postings != 4 || calls.Load() != 3 || failures != 0 || reserved {
					t.Fatalf("complete inventory/lifecycle changed: %+v", result)
				}
				if n := f.r.ZCard(ctx, "ft_scrapes_simple:join.com").Val(); n != 3 {
					t.Fatal("separate urgent details missing", n)
				}
				var contents int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id IN (SELECT id FROM job_posting WHERE board_id=$1::uuid)", f.board).Scan(&contents); err != nil || contents != 0 {
					t.Fatal("URL inventory wrote detail content", err)
				}
			} else {
				if !active || missing != 3 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("incomplete/reserved/gone response changed posting inventory")
				}
				if mode == "later_failure" {
					if result.Cycle.Status != "failed" || failures != 1 || reserved || calls.Load() != 5 {
						t.Fatal("failed required page became success/gone", result.Cycle, calls.Load())
					}
				} else if mode == "header" || mode == "meta" {
					if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || calls.Load() != 4 || evidence == nil || !strings.Contains(*evidence, "https://join.com/policy-page") || !strings.Contains(*evidence, "actual-page-policy") || !strings.Contains(*evidence, `"source": "`+mode+`"`) {
						t.Fatal("actual reservation page/source lost", result.Cycle, evidence)
					}
				} else if result.Cycle.Status != "gone_pending" || failures != 0 || reserved || calls.Load() != 1 || due.Before(time.Now().Add(5*time.Hour)) {
					t.Fatal("first-page disappearance contract changed", result.Cycle, due)
				}
			}
			assertRichDeadlineAndLease(t, f, "join")
		})
	}
}
