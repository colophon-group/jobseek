package worker

import (
	"context"
	"net/http"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealRenderedMyJobShopPreservesEmptyProofAndURLOnlyIntent(t *testing.T) {
	for _, mode := range []string{"complete", "explicit-empty", "missing-marker", "publisher", "incomplete"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"render":true,"my_job_shop":true,"link_selector":"a.job","empty_states":[{"selector":"p.empty","exact_text":"No jobs"}],"scraper_type":"json-ld"}`, queue.Browser, queue.Simple)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				calls++
				source := `<a class="job" href="/jobs/new">Engineer</a>`
				if mode == "explicit-empty" {
					source = `<p class="empty">No jobs</p>`
				} else if mode == "missing-marker" {
					source = `<p>Listing not loaded</p>`
				}
				value := heldRenderedResult(source, p.Endpoint, 200)
				if mode == "publisher" {
					v := "1"
					value.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: &v}
				} else if mode == "incomplete" {
					value.GetSuccess().Html.Complete = false
				}
				return parseHeldRenderedMonitor(ctx, p, c, value)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("held inventory fetched extra HTTP") })
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits, renderer)
			if err != nil || result == nil || !result.Settled || calls != 1 || preparer.at != 0 {
				t.Fatal("URL-only rendered cycle did not settle", err)
			}
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if inserted != 1 || failures != 0 || reserved || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 1 {
					t.Fatal("complete URL-only intent or detail queue changed")
				}
			} else if mode == "explicit-empty" {
				if inserted != 0 || failures != 0 || reserved || result.Cycle.Status != "succeeded" {
					t.Fatal("explicit empty marker lost normal lifecycle")
				}
			} else if inserted != 0 || missing != 0 || reserved != (mode == "publisher") || mode != "publisher" && failures != 1 {
				t.Fatal("unproved document gained publication or absence authority")
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
		})
	}
}
