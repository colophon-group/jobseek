package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealSelfIdentifyingDOMPreservesInventoryPolicyAndSettlement(t *testing.T) {
	raw, err := os.ReadFile("../ordinary-queue/testdata/self_identifying_cma_metadata.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"complete", "publisher", "unproved_empty", "incomplete"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "dom", string(raw), "https://www.airbelgium.com/jobs/", queue.Browser, queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("original DOM browser claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			html := `<a href="/first-officer/">First Officer</a><a href="/lmcc-engineer/">LMCC Engineer</a><a href="/material-controller/">Material Controller</a>`
			if dir := os.Getenv("JOBSEEK_SELF_IDENTIFYING_DOM_CAPTURE_DIR"); dir != "" {
				body, err := os.ReadFile(filepath.Join(dir, "cma-cgm-air-belgium-lightpanda.json"))
				var physical struct {
					Status  string
					Cleanup bool `json:"child_cleanup_proved"`
					Result  struct {
						HTML   string
						Status int
						Final  string `json:"final_url"`
					}
				}
				if err != nil || json.Unmarshal(body, &physical) != nil || physical.Status != "succeeded" || !physical.Cleanup || physical.Result.Status != 200 || physical.Result.Final != "https://www.airbelgium.com/jobs/" {
					t.Fatal("complete physical public document unavailable")
				}
				html = physical.Result.HTML
			}
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				_, navigation, err := queue.RenderedDOMMonitorOptions(c)
				if err != nil || navigation["stealth"] != nil {
					t.Fatal("source-bound self-identifying navigation lost", err)
				}
				if mode == "unproved_empty" {
					html = "<html><body>Temporarily unavailable</body></html>"
				}
				value := heldRenderedResult(html, p.Endpoint, 200)
				if mode == "publisher" {
					one := "1"
					value.GetSuccess().ResourcePolicy.TdmReservationHeader = &one
				}
				if mode == "incomplete" {
					value.GetSuccess().Html.Complete = false
				}
				return parseHeldRenderedMonitor(ctx, p, c, value)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("rendered inventory issued direct HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("DOM cycle did not settle", err)
			}
			var count, missing, failures int
			var reserved, active bool
			if err := f.pg.QueryRow(ctx, "SELECT (SELECT count(*) FROM job_posting WHERE board_id=$1::uuid),consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&count, &failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if count != 4 || result.Batches.Inserted != 3 || failures != 0 || missing != 1 || reserved {
					t.Fatal("complete DOM inventory lost", count, result.Batches.Inserted, failures, missing)
				}
				if f.r.ZCard(ctx, "ft_scrapes_browser:www.airbelgium.com").Val() != 3 {
					t.Fatal("original browser detail routing lost")
				}
			} else {
				if count != 1 || result.Batches.Inserted != 0 || !active || missing != 0 {
					t.Fatal("uncertain or reserved DOM acquired absence authority")
				}
				if mode == "publisher" && (!reserved || failures != 0) || mode != "publisher" && (reserved || failures != 1) {
					t.Fatal("failure or reservation settlement changed", result.Cycle.Status, failures, reserved)
				}
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
		})
	}
}
