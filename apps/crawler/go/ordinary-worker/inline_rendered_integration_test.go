package worker

import (
	"context"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"strings"
	"testing"
)

func TestRealRenderedInlinePersistsCanonicalFieldsAndRejectsInvalidInventory(t *testing.T) {
	for _, mode := range []string{"rich", "actions", "header", "meta", "root404", "zero-proof", "explicit-empty", "challenge", "malformed"} {
		t.Run(mode, func(t *testing.T) {
			md := `{"render":true,"scraper_type":"skip","steps":[{"tag":"h2","field":"title"},{"tag":"p","field":"description","html":true,"stop_tag":"h2"}],"defaults":{"locations":["Zurich"]},"require_zero_proof":true`
			if mode == "explicit-empty" {
				md += `,"empty_selector":".empty","empty_text":"No vacancies"`
			}
			if mode == "actions" {
				md += `,"actions":[{"action":"evaluate","script":"() => window.ready = true","required":true},{"action":"wait","ms":0}]`
			}
			md += `}`
			f := privateRichPipelineFixture(t, "inline", md, queue.Browser)
			ctx := context.Background()
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal(e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				calls++
				body := `<h2>Senior Software Engineer</h2><p>Go and PostgreSQL in Zurich.</p>`
				status := uint32(200)
				switch mode {
				case "meta":
					body = `<meta name="tdm-reservation" content="1">` + body
				case "root404":
					status = 404
				case "zero-proof":
					body = `<h1>No accepted rows</h1>`
				case "explicit-empty":
					body = `<div class="empty">No vacancies</div>`
				case "challenge":
					body = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
				}
				v := heldRenderedResult(body, p.Endpoint, status)
				if mode == "header" {
					v.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: func() *string { s := "1"; return &s }()}
				}
				if mode == "malformed" {
					v.GetSuccess().Html.Complete = false
				}
				return parseHeldRenderedMonitor(ctx, p, c, v)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered Inline used direct HTTP") })
			r, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if e != nil || r == nil || !r.Settled || calls != 1 {
				t.Fatal(r, e, calls)
			}
			assertRichDeadlineAndLease(t, f, "inline", queue.Browser)
			var count, missing, failures, empty int
			var reserved bool
			if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty, &reserved); e != nil {
				t.Fatal(e)
			}
			if mode == "rich" || mode == "actions" {
				var title, html string
				var locations []int32
				if e = f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &locations, &html); e != nil {
					t.Fatal(e)
				}
				if count != 1 || missing != 1 || failures != 0 || title != "Senior Software Engineer" || len(locations) != 1 || locations[0] != 2 || !strings.Contains(html, "Go and PostgreSQL") {
					t.Fatal("rendered canonical fields lost")
				}
				return
			}
			if count != 0 || missing != 0 {
				t.Fatal("invalid/empty inventory finalized absence", count, missing)
			}
			if mode == "header" || mode == "meta" {
				if !reserved || failures != 0 {
					t.Fatal("policy outcome lost")
				}
				return
			}
			if mode == "explicit-empty" {
				if empty != 1 || failures != 0 {
					t.Fatal("verified zero confirmation lost")
				}
				return
			}
			if failures != 1 {
				t.Fatal("invalid inventory did not fail", failures)
			}
		})
	}
}
