package worker

import (
	"context"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestRealExplicitNoScrapeClearsOnlyScheduleWithoutFetching(t *testing.T) {
	for _, worker := range []queue.WorkerType{queue.Simple, queue.Browser} {
		for _, mode := range []string{"active", "reserved", "inactive", "already_unscheduled", "changed_binding", "changed_source"} {
			t.Run(string(worker)+"/"+mode, func(t *testing.T) {
				f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"skip","scraper_config":{"proxy":true}}`, "", worker)
				ctx := context.Background()
				sql := ""
				switch mode {
				case "reserved":
					sql = "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid"
				case "inactive":
					sql = "UPDATE job_posting SET is_active=false WHERE id=$1::uuid"
				case "already_unscheduled":
					sql = "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid"
				case "changed_source":
					sql = "UPDATE job_posting SET source_url=source_url||'?changed=1' WHERE id=$1::uuid"
				}
				if sql != "" {
					if _, e := f.pg.Exec(ctx, sql, f.original); e != nil {
						t.Fatal(e)
					}
				}
				if mode == "changed_binding" {
					if _, e := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"scraper_config":{"enrich":["description"]}}'::jsonb WHERE id=(SELECT board_id FROM job_posting WHERE id=$1::uuid)`, f.original); e != nil {
						t.Fatal(e)
					}
				}
				var before, after string
				snapshot := `SELECT (to_jsonb(p)-'next_scrape_at'-'leased_until')::text FROM job_posting p WHERE id=$1::uuid`
				if e := f.pg.QueryRow(ctx, snapshot, f.original).Scan(&before); e != nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				calls := 0
				client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { calls++; t.Error("SQL-only clear fetched a page") })
				render := heldRenderedDetail(func(context.Context, queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
					calls++
					t.Error("SQL-only clear rendered a page")
					return nil, nil, queue.ErrConfiguration
				})
				got, e := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits, render)
				if calls != 0 {
					t.Fatal("clear consumer used origin transport")
				}
				if e2 := f.pg.QueryRow(ctx, snapshot, f.original).Scan(&after); e2 != nil {
					t.Fatal(e2)
				}
				if before != after {
					t.Fatal("clear consumer changed canonical posting data")
				}
				if mode == "changed_binding" || mode == "changed_source" {
					if e == nil || got.Settled {
						t.Fatal("changed canonical binding retained clear authority")
					}
					return
				}
				if e != nil || !got.Settled || got.Cycle.Status != "unscheduled" {
					t.Fatal("clear did not settle", e, got)
				}
				var due, lease bool
				if e := f.pg.QueryRow(ctx, "SELECT next_scrape_at IS NULL,leased_until IS NULL FROM job_posting WHERE id=$1::uuid", f.original).Scan(&due, &lease); e != nil || !due || !lease {
					t.Fatal("canonical schedule/lease retained", e)
				}
				domain := claim.Descriptor().Domain
				if f.r.ZCard(ctx, "inflight:"+string(worker)).Val() != 0 || f.r.HLen(ctx, "inflight_tokens:"+string(worker)).Val() != 0 || f.r.ZCard(ctx, "scrapes_"+string(worker)+":"+domain).Val() != 0 {
					t.Fatal("clear consumer rescheduled or retained its attempt")
				}
			})
		}
	}
}
