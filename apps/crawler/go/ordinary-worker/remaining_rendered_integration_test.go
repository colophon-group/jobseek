package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const remainingRevolutMetadata = `{"render":true,"stealth":true,"wait":"domcontentloaded","path":"props.pageProps.positions","url_template":"https://www.revolut.com/careers/position/{slug}-{id}/","slug_fields":["text"],"fields":{"title":"text","locations":"locations[].name","metadata.team":"team"},"scraper_type":"nextdata","scraper_config":{"render":true,"path":"props.pageProps.position","fields":{"title":"text","locations":"locations[].name","description":"description"}}}`
const remainingIIHFMetadata = `{"render":true,"steps":[{"tag":"h3","attr":"class=s-sub-title","field":"title"},{"tag":"div","attr":"class=s-content","html":true,"field":"description","stop_tag":"h3"}],"defaults":{"locations":["Zurich, CH"]},"empty_selector":".s-content","empty_text":"Details of all future job opportunities will be advertised here","empty_requires_no_jobs":true,"fetch_urls":["https://canada-central.iihf.com/en/static/5082/jobs","https://eu-west.iihf.com/en/static/5082/jobs","https://www.iihf.com/en/static/5082/jobs"],"scraper_type":"skip"}`

func TestRealRemainingRenderedPresetsPreserveSettlementAndPolicy(t *testing.T) {
	for _, provider := range []string{"nextdata", "inline"} {
		t.Run(provider, func(t *testing.T) {
			modes := []string{"complete", "header", "meta", "malformed", "missing_policy", "changed_binding"}
			if provider == "inline" {
				modes = append(modes, "first503", "all404")
			}
			for _, mode := range modes {
				t.Run(mode, func(t *testing.T) {
					metadata, board, detailWorker := remainingIIHFMetadata, "https://www.iihf.com/en/static/5082/jobs", queue.Simple
					if provider == "nextdata" {
						metadata, board, detailWorker = remainingRevolutMetadata, "https://www.revolut.com/careers/", queue.Browser
					}
					f := privateRichPipelineFixtureURL(t, provider, metadata, board, queue.Browser, detailWorker)
					ctx := context.Background()
					if _, e := f.pg.Exec(ctx, "UPDATE job_board SET empty_check_count=3 WHERE id=$1::uuid", f.board); e != nil {
						t.Fatal(e)
					}
					if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
						t.Fatal(e)
					}
					claim, e := f.a.Claim(ctx, queue.Browser)
					if e != nil || claim == nil {
						t.Fatal(e)
					}
					circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if e != nil {
						t.Fatal(e)
					}
					calls := 0
					source := `<h3 class="s-sub-title">Senior Software Engineer</h3><div class="s-content"><p>Build systems in Zurich.</p></div>`
					if provider == "nextdata" {
						b, _ := json.Marshal(map[string]any{"props": map[string]any{"pageProps": map[string]any{"positions": []any{map[string]any{"id": f.company, "text": "Senior Software Engineer", "locations": []any{map[string]any{"name": "Zurich"}}, "team": "Engineering"}}}}})
						source = `<script id="__NEXT_DATA__">` + string(b) + `</script>`
					}
					value := func(p queue.GreenhouseMonitorProfile) *runtimev1.BrowserResult {
						calls++
						status := uint32(200)
						html := source
						if mode == "first503" && calls == 1 {
							status = 503
						}
						if mode == "all404" {
							status = 404
						}
						if mode == "meta" {
							html = `<meta name="tdm-reservation" content="1">`
						}
						v := heldRenderedResult(html, p.Endpoint, status)
						if mode == "header" {
							one := "1"
							v.GetSuccess().ResourcePolicy.TdmReservationHeader = &one
						}
						if mode == "malformed" {
							v.GetSuccess().Html.Complete = false
						}
						if mode == "missing_policy" {
							v.GetSuccess().ResourcePolicy = nil
						}
						if mode == "changed_binding" {
							if _, e := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"wait":"load"}'::jsonb WHERE id=$1::uuid`, f.board); e != nil {
								t.Fatal(e)
							}
						}
						return v
					}
					var renderer renderedMonitorClient
					if provider == "nextdata" {
						renderer = heldNextdataMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string, endpoint string) nextdataPage {
							o, _, e := queue.RenderedNextdataMonitorOptions(c)
							if e != nil {
								t.Fatal(e)
							}
							return parseHeldNextdataPage(ctx, p, o, value(p))
						})
					} else {
						renderer = heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
							o, _, e := queue.RenderedInlineMonitorOptions(c)
							if e != nil {
								t.Fatal(e)
							}
							return collectRenderedInlineCandidates(ctx, p, c, o, func(p queue.GreenhouseMonitorProfile, _ int) (*runtimev1.BrowserResult, error) { return value(p), nil })
						})
					}
					client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("browser monitor used HTTP") })
					got, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
					if mode == "changed_binding" {
						if e == nil || got.Settled {
							t.Fatal("changed source binding acquired write authority")
						}
						return
					}
					want := "succeeded"
					if mode == "header" || mode == "meta" {
						want = "publisher_reserved"
					}
					if mode == "malformed" || mode == "missing_policy" || mode == "all404" {
						want = "failed"
					}
					if e != nil || got == nil || !got.Settled || got.Cycle.Status != want {
						t.Fatal("preset settlement differs", mode, e, got)
					}
					var count, active, missing, empty, failures, gone int
					if e := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active),max(missing_count) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active, &missing); e != nil {
						t.Fatal(e)
					}
					if e := f.pg.QueryRow(ctx, "SELECT empty_check_count,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&empty, &failures, &gone); e != nil {
						t.Fatal(e)
					}
					if want != "succeeded" && (count != 1 || active != 1 || missing != 3 || empty != 3 || gone != 0) {
						t.Fatalf("failed/reserved preset acquired absence: count%d active%d missing%d empty%d gone%d", count, active, missing, empty, gone)
					}
					if want == "succeeded" && (count != 2 || active != 1 || empty != 0 || got.Batches.Inserted != 1) {
						t.Fatal("complete canonical preset inventory changed", count, active, empty, got)
					}
					if mode == "first503" && calls != 2 || mode == "all404" && calls != 3 {
						t.Fatal("ordered candidate conservation changed", calls)
					}
					if (mode == "header" || mode == "meta" || mode == "malformed" || mode == "missing_policy") && calls != 1 {
						t.Fatal("uncertain or reserved document triggered fallback", calls)
					}
					if strings.Contains(want, "failed") && failures != 1 {
						t.Fatal("unproved inventory did not settle failure", failures)
					}
					assertRichDeadlineAndLease(t, f, provider, queue.Browser)
				})
			}
		})
	}
}
