package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealItemListHeldInventoryPublishesOnlyCompleteURLIntent(t *testing.T) {
	for _, mode := range []string{"complete", "missing", "late-null", "reserved", "empty"} {
		t.Run(mode, func(t *testing.T) {
			md, _ := json.Marshal(map[string]any{"source": "browser", "browser_expression": api.ItemListBrowserExpression, "path": "jobs", "url_template": "{url}", "scraper_type": "json-ld"})
			f := privateRichPipelineFixture(t, "nextdata", string(md), queue.Browser, queue.Simple)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			source := "https://example.com/jobs/" + f.company
			calls := 0
			renderer := heldNextdataMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string, endpoint string) nextdataPage {
				calls++
				items := []any{map[string]any{"url": source, "name": "Engineer"}, map[string]any{"url": "https://example.com/pool", "name": "Pool de talentos"}}
				if mode == "late-null" {
					items = append(items, nil)
				}
				if mode == "empty" {
					items = []any{}
				}
				raw, _ := json.Marshal(map[string]any{"@type": "ItemList", "itemListElement": items})
				html := `<script type="application/ld+json">` + string(raw) + `</script>`
				if mode == "missing" {
					html = "<p>No list marker</p>"
				}
				value := heldRenderedResult(html, endpoint, 200)
				if mode == "reserved" {
					v := "1"
					value.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: &v}
				}
				options, _, e := queue.RenderedNextdataMonitorOptions(c)
				if e != nil {
					t.Fatal("held options unsupported", e)
				}
				return parseHeldNextdataPage(ctx, p, options, value)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("held source issued direct HTTP") })
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits, renderer)
			if err != nil || result == nil || !result.Settled || calls != 1 || preparer.at != 0 {
				t.Fatal("URL-only held cycle did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "nextdata", queue.Browser)
			var count, failures, missing int
			var reserved bool
			if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
				t.Fatal(e)
			}
			if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
				t.Fatal(e)
			}
			if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if count != 1 || failures != 0 || reserved {
					t.Fatal("complete held inventory failed")
				}
				var id string
				var titles, locales []string
				var due bool
				if e := f.pg.QueryRow(ctx, "SELECT id::text,titles,locales,next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &titles, &locales, &due); e != nil {
					t.Fatal(e)
				}
				score, e := f.r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Result()
				if len(titles) != 0 || len(locales) != 0 || !due || e != nil || score != 0 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("URL-only intent acquired rich content or lost detail queue")
				}
			} else if mode == "empty" {
				if count != 0 || failures != 0 || reserved {
					t.Fatal("explicit empty list lost normal lifecycle")
				}
			} else if count != 0 || missing != 0 || reserved != (mode == "reserved") || mode != "reserved" && failures != 1 {
				t.Fatal("failed held inventory acquired partial/absence authority", mode)
			}
		})
	}
}
