package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDocumentInteractionsInventoryAndAtomicFailure(t *testing.T) {
	for _, kind := range []string{"click", "wait_for", "repeat", "paginate_collect", "long-pipeline"} {
		t.Run(kind, func(t *testing.T) {
			for _, mode := range []string{"complete", "failed", "reserved"} {
				t.Run(mode, func(t *testing.T) {
					action := map[string]any{"action": kind, "selector": "button"}
					if kind == "wait_for" {
						action["state"] = "attached"
					}
					if kind == "paginate_collect" {
						delete(action, "selector")
						action["next_selector"] = "a.next"
					}
					pipeline := []any{action}
					if kind == "long-pipeline" {
						pipeline = []any{}
						for i := 0; i < 36; i++ {
							pipeline = append(pipeline, map[string]any{"action": "wait", "ms": 0})
						}
					}
					md, _ := json.Marshal(map[string]any{"render": true, "actions": pipeline, "url_filter": "/jobs/", "scraper_type": "json-ld"})
					f := privateRichPipelineFixture(t, "dom", string(md), queue.Browser, queue.Simple)
					ctx := context.Background()
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
					claim, err := f.a.Claim(ctx, queue.Browser)
					if err != nil || claim == nil {
						t.Fatal("interaction claim", err)
					}
					circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if err != nil {
						t.Fatal(err)
					}
					renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
						value := heldRenderedResult(`<a href="/jobs/engineer">Engineer</a>`, p.Endpoint, 200)
						if mode == "failed" {
							value = &runtimev1.BrowserResult{ContractVersion: "crawler.runtime/v1", Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Error{Error: &runtimev1.BrowserFailure{Error: &runtimev1.RuntimeError{Code: runtimev1.ErrorCode_ERROR_CODE_INTERNAL, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY}}}}
						}
						if mode == "reserved" {
							reserved := "1"
							value.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reserved}
						}
						return parseHeldRenderedMonitor(ctx, p, c, value)
					})
					client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("interaction bypassed its browser route") })
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
					if err != nil || result == nil || !result.Settled {
						t.Fatal("interaction settlement", err)
					}
					var active, reserved bool
					var missing, count, failures int
					if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); err != nil {
						t.Fatal(err)
					}
					if mode == "complete" {
						if active || missing != 4 || count != 2 || failures != 0 || result.Batches.Inserted != 1 {
							t.Fatal("complete inventory effects lost")
						}
					} else {
						if !active || missing != 3 || count != 1 {
							t.Fatal("partial inventory gained absence or insertion authority")
						}
						if mode == "reserved" {
							if !reserved || failures != 0 || result.Cycle.Status != "publisher_reserved" {
								t.Fatal("publisher precedence lost")
							}
						} else if failures != 1 || result.Cycle.Status != "failed" {
							t.Fatal("required action failure became success")
						}
					}
					assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
				})
			}
		})
	}
}
