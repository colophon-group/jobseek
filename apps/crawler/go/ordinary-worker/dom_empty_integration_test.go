package worker

import (
	"context"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"testing"
)

func TestRealDOMExplicitEmptyPreservesFailedInventory(t *testing.T) {
	for _, rendered := range []bool{false, true} {
		for _, mode := range []string{"jobs", "verified-zero", "missing-zero", "wrong-first-marker"} {
			t.Run(fmt.Sprintf("rendered=%v/%s", rendered, mode), func(t *testing.T) {
				md := `{"link_selector":"a.job","empty_selector":".empty","empty_text":"No vacancies","scraper_type":"skip"`
				worker := queue.Simple
				if rendered {
					worker = queue.Browser
					md += `,"render":true`
				}
				md += `}`
				f := privateRichPipelineFixture(t, "dom", md, worker)
				ctx := context.Background()
				claim, e := f.a.Claim(ctx, worker)
				if e != nil || claim == nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				body := `<p>No open jobs</p>`
				if mode == "jobs" {
					body = `<a class="job" href="/jobs/` + f.company + `">Engineer</a>`
				}
				if mode == "verified-zero" {
					body = `<p class="empty"> NO <span>Vacancies</span> today</p>`
				}
				if mode == "wrong-first-marker" {
					body = `<p class="empty">Other</p><p class="empty">No vacancies</p>`
				}
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					if rendered {
						t.Error("rendered DOM used direct HTTP")
					}
					calls++
					fmt.Fprint(w, body)
				})
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					calls++
					return parseHeldRenderedMonitor(ctx, p, c, heldRenderedResult(body, p.Endpoint, 200))
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if e != nil || result == nil || !result.Settled || calls != 1 {
					t.Fatal(result, e, calls)
				}
				assertRichDeadlineAndLease(t, f, "dom", worker)
				var count, missing, failures, empty int
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty); e != nil {
					t.Fatal(e)
				}
				if mode == "jobs" {
					if count != 1 || missing != 1 || failures != 0 {
						t.Fatal("accepted nonempty inventory lost")
					}
					return
				}
				if count != 0 || missing != 0 {
					t.Fatal("empty attempt finalized absence", count, missing)
				}
				if mode == "verified-zero" {
					if failures != 0 || empty != 1 {
						t.Fatal("zero confirmation lost")
					}
					return
				}
				if failures != 1 || empty != 0 {
					t.Fatal("missing/contradictory marker treated as zero")
				}
			})
		}
	}
}
