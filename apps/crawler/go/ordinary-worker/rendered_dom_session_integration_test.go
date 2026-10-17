package worker

import (
	"context"
	"fmt"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"testing"
)

func TestRealHeldDOMPaginationPreservesInventoryPolicyAndFailureSettlement(t *testing.T) {
	for _, mode := range []string{"complete", "late404", "late400", "late503", "empty200", "challenge403", "tail-policy", "root-policy"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"render":true,"pagination":{"browser":true,"param_name":"page","max_pages":3},"url_filter":"/jobs/","scraper_type":"json-ld"}`, queue.Browser, queue.Simple)
			ctx := context.Background()
			if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
				t.Fatal(e)
			}
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal("claim unavailable", e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			pages := 0
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				listing, _, e := queue.RenderedDOMMonitorOptions(c)
				if e != nil {
					t.Fatal(e)
				}
				return collectDOMListingPages(ctx, p, c, listing, func(current queue.GreenhouseMonitorProfile, options dom.ListingConfig, later bool) (RichDiscovery, error) {
					pages++
					body, status, header := `<a href="/jobs/root">Root</a>`, 200, ""
					if later {
						body = `<a href="/jobs/tail">Tail</a>`
						switch mode {
						case "late404":
							status = 404
						case "late400":
							status = 400
						case "late503":
							status = 503
						case "empty200":
							body = ""
						case "challenge403":
							status = 403
							body = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
						case "tail-policy":
							header = "1"
						}
						if pages == 3 {
							body = "<p>End</p>"
						}
					}
					if !later && mode == "root-policy" {
						header = "1"
					}
					return parseHeldDOMPage(ctx, current, options, heldDOMPageBytes(t, current.Endpoint, body, status, header), later)
				})
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("held pagination escaped to worker HTTP") })
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("native settlement failed", e)
			}
			var active, reserved bool
			var missing, count, failures int
			if e = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); e != nil {
				t.Fatal(e)
			}
			switch mode {
			case "complete":
				if result.Batches.Inserted != 2 || count != 3 || active || missing != 4 || failures != 0 || pages != 3 {
					t.Fatal("complete inventory lost", fmt.Sprint(result), count, missing, pages)
				}
			case "late404", "late400":
				if result.Batches.Inserted != 1 || count != 2 || failures != 0 || pages != 2 {
					t.Fatal("terminal tail changed")
				}
			default:
				if !active || missing != 3 || count != 1 {
					t.Fatal("failed or reserved tail published prefix/absence")
				}
				if mode == "tail-policy" || mode == "root-policy" {
					if !reserved || failures != 0 {
						t.Fatal("reservation became failure")
					}
				} else if failures != 1 {
					t.Fatal("failure disposition lost")
				}
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
		})
	}
}
