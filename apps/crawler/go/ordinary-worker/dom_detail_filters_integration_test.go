package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMInactiveDetailFilterConservation(t *testing.T) {
	realDOMDetailFilterConservation(t, true)
}
func TestRealDOMExcludedDetailSelectorConservation(t *testing.T) {
	realDOMDetailFilterConservation(t, false)
}
func realDOMDetailFilterConservation(t *testing.T, inactive bool) {
	for _, route := range []string{"direct", "proxy"} {
		modes := []string{"complete", "failed-detail", "reserved-detail"}
		if inactive {
			modes = append(modes, "marker-drift", "all-inactive")
		}
		for _, mode := range modes {
			t.Run(route+"/"+mode, func(t *testing.T) {
				md := `{"link_selector":"a.job","url_filter":"/jobs/","exclude_detail_selector":".inactive","scraper_type":"skip"}`
				if inactive {
					md = `{"link_selector":"a.job","url_filter":"/jobs/","inactive_detail_states":[{"selector":".inactive","exact_text":"Closed"}],"scraper_type":"skip"}`
				}
				worker := queue.Simple
				if route == "proxy" {
					md = proxyFixtureMetadata(t, md, true)
				}
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
				listing := fmt.Sprintf(`<a class="job" href="/jobs/%s/1">One</a><a class="job" href="/jobs/%s/2">Two</a>`, f.company, f.company)
				var requests atomic.Int32
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests.Add(1)
					if r.URL.Path == "/careers" {
						if route == "rendered" {
							t.Error("rendered root used HTTP")
						}
						fmt.Fprint(w, listing)
						return
					}
					if !strings.HasPrefix(r.URL.Path, "/jobs/"+f.company+"/") {
						t.Error("unbound verification URL")
					}
					if strings.HasSuffix(r.URL.Path, "/2") {
						if mode == "failed-detail" {
							w.WriteHeader(503)
							fmt.Fprint(w, "unavailable")
							return
						}
						if mode == "reserved-detail" {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(404)
							return
						}
						if mode == "marker-drift" {
							fmt.Fprint(w, `<div class="inactive">Pending</div>`)
							return
						}
						fmt.Fprint(w, `<div class="inactive">Closed</div>`)
						return
					}
					if mode == "all-inactive" {
						fmt.Fprint(w, `<div class="inactive">Closed</div>`)
						return
					}
					fmt.Fprint(w, "<html>Active job page</html>")
				}))
				if route == "proxy" {
					client = credentialedProxyFixture(t, client)
				}
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					return collectRenderedDOMPages(ctx, p, c, client.client, func(int) (*runtimev1.BrowserResult, error) {
						requests.Add(1)
						return heldRenderedResult(listing, p.Endpoint, 200), nil
					})
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("verification failed to settle", e, result)
				}
				assertRichDeadlineAndLease(t, f, "dom", worker)
				var inserted, missing, failures int
				var reserved bool
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
					t.Fatal(e)
				}
				if mode == "all-inactive" {
					var empty int
					if err := f.pg.QueryRow(ctx, "SELECT empty_check_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&empty); err != nil {
						t.Fatal(err)
					}
					if inserted != 0 || missing != 0 || failures != 0 || reserved || requests.Load() != 3 || result.Cycle.Status != "succeeded" || empty != 1 {
						t.Fatal("all-inactive canonical/empty effect changed", inserted, missing, failures, result.Cycle.Status)
					}
					return
				}
				if mode == "complete" {
					if inserted != 1 || missing != 1 || failures != 0 || reserved || requests.Load() != 3 {
						t.Fatal("verified subset canonical/schedule effect changed", inserted, missing, failures, reserved, requests.Load())
					}
					return
				}
				wantFailures := 1
				wantStatus := "failed"
				if mode == "reserved-detail" {
					wantFailures = 0
					wantStatus = "publisher_reserved"
				}
				if inserted != 0 || missing != 0 || failures != wantFailures || reserved != (mode == "reserved-detail") || result.Cycle.Status != wantStatus {
					t.Fatal("failed/reserved verification changed canonical jobs", inserted, missing, failures, reserved, result.Cycle.Status)
				}
			})
		}
	}
}
