package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealMixedDOMPaginationPreservesWholeInventoryPolicyAndSettlement(t *testing.T) {
	for _, mode := range []string{"success", "repeat", "late404", "late403", "transient403", "late503", "empty200", "tail-reservation", "root-reservation", "root-manifest", "root-challenge", "challenge-retry", "foreign-redirect", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"render":true,"pagination":{"param_name":"page","max_pages":3,"transport_attempts":1},"url_filter":"/jobs/","scraper_type":"json-ld"}`
			if mode == "transient403" {
				metadata = strings.Replace(metadata, `"param_name":"page"`, `"param_name":"page","transient_403":true`, 1)
			}
			f := privateRichPipelineFixture(t, "dom", metadata, queue.Browser, queue.Simple)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal(err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			requests := []string{}
			navigations := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, r.URL.String())
				if r.URL.Path != "/careers" || r.URL.Query().Get("page") == "" {
					t.Error("tail escaped configured pages", r.URL.String())
				}
				if r.URL.Query().Get("page") == "3" {
					fmt.Fprint(w, `<p>End</p>`)
					return
				}
				switch mode {
				case "late404":
					w.WriteHeader(404)
					return
				case "late403", "transient403":
					w.WriteHeader(403)
					return
				case "late503":
					w.WriteHeader(503)
					return
				case "empty200":
					return
				case "tail-reservation":
					w.Header().Set("TDM-Reservation", "1")
				case "foreign-redirect":
					http.Redirect(w, r, "https://foreign.example/steal", 302)
					return
				}
				if mode == "repeat" {
					fmt.Fprint(w, `<a href="/jobs/root">Root</a>`)
					return
				}
				fmt.Fprint(w, `<a href="/jobs/tail">Tail</a><a href="/jobs/root">Repeat</a>`)
			})
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				return collectRenderedDOMPages(ctx, p, c, client.client, func(attempt int) (*runtimev1.BrowserResult, error) {
					navigations++
					if mode == "cancelled" {
						return nil, context.Canceled
					}
					source := `<a href="/jobs/root">Root</a>`
					if mode == "root-reservation" {
						source = `<meta name="tdm-reservation" content="1">`
					}
					if mode == "root-challenge" || mode == "challenge-retry" && attempt == 0 {
						source = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
					}
					v := heldRenderedResult(source, p.Endpoint, 200)
					if mode == "root-manifest" {
						v.GetSuccess().Html.Complete = false
					}
					return v, nil
				})
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
			if err != nil || !result.Settled {
				t.Fatal(result, err)
			}
			var active, reserved bool
			var missing, count, failures int
			if err = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "success", "challenge-retry":
				if result.Batches.Inserted != 2 || count != 3 || active || missing != 4 || failures != 0 || len(requests) != 2 {
					t.Fatal("full mixed inventory lost", result, count, requests)
				}
			case "repeat", "late404", "late403":
				if result.Batches.Inserted != 1 || count != 2 || failures != 0 || len(requests) != 1 {
					t.Fatal("tail termination lost", result, count, requests)
				}
			default:
				if !active || missing != 3 || count != 1 {
					t.Fatal("failed tail published prefix or absence", result, count)
				}
				if strings.Contains(mode, "reservation") {
					if !reserved || failures != 0 {
						t.Fatal("publisher policy lost", result)
					}
				} else if failures != 1 {
					t.Fatal("failed inventory not recorded", result)
				}
			}
			if mode == "root-reservation" || mode == "root-manifest" || mode == "root-challenge" || mode == "cancelled" {
				if len(requests) != 0 {
					t.Fatal("invalid root fetched tail", requests)
				}
			}
			if mode == "challenge-retry" || mode == "root-challenge" {
				if navigations != 2 {
					t.Fatal("root challenge budget", navigations)
				}
			} else if navigations != 1 {
				t.Fatal("tail rendered instead of HTTP", navigations)
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:browser").Val() != 0 {
				t.Fatal("browser claim not conserved")
			}
		})
	}
}
