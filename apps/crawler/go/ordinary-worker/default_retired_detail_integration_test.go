package worker

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"github.com/jackc/pgx/v5"
)

func TestRealDefaultRetiredFormerBrowserDetailMakesNoTransportRequest(t *testing.T) {
	for _, family := range []string{"workday", "api_sniffer"} {
		for _, variant := range []string{"unscheduled", "inactive", "reactivated", "unscheduled-retired-url", "inactive-retired-url", "reactivated-retired-url", "unscheduled-retired-step", "inactive-retired-step", "reactivated-retired-step"} {
			t.Run(family+"/"+variant, func(t *testing.T) {
				mode := strings.Split(variant, "-retired-")[0]
				f, initialAuthority, old, _ := workdayDetailFixture(t, false)
				ctx := context.Background()
				receipt, err := initialAuthority.Write(ctx, old, true, func(ctx context.Context, tx pgx.Tx) error {
					_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", f.original)
					return err
				})
				if err != nil {
					t.Fatal("initial fixture receipt failed", err)
				}
				if err := initialAuthority.Settle(ctx, old, receipt); err != nil {
					t.Fatal("initial fixture claim release failed", err)
				}
				source := "https://fixture.wd1.myworkdayjobs.com/Careers/job/City/Engineer_R100"
				if family == "api_sniffer" {
					source = "https://careers.example.net/job?itemId=123"
					metadata := `{"api_url":"https://api.example.net/jobs","json_path":"jobs","url_field":"url","fields":{"title":"title","description":"description"},"scraper_type":"api_sniffer","scraper_config":{"api_url":"https://api.example.net/detail/{item_id}","url_pattern":"[?&]itemId=(?P<item_id>[^&#]+)","json_path":"job","fields":{"title":"title","description":"description"},"enrich":["description"]}}`
					if _, err := f.pg.Exec(ctx, `UPDATE job_board SET crawler_type='api_sniffer',board_url='https://careers.example.net/jobs',metadata=$2::jsonb,throttle_key='careers.example.net',monitor_needs_browser=false,scraper_needs_browser=false WHERE id=$1::uuid`, f.board, metadata); err != nil {
						t.Fatal(err)
					}
					config, err := f.r.HGetAll(ctx, "board:"+f.board).Result()
					if err != nil {
						t.Fatal(err)
					}
					config["crawler_type"], config["board_url"], config["metadata"] = "api_sniffer", "https://careers.example.net/jobs", metadata
					config["domain"], config["throttle_key"] = "careers.example.net", "careers.example.net"
					config["monitor_needs_browser"], config["scraper_needs_browser"] = "0", "0"
					if err = f.r.HSet(ctx, "board:"+f.board, config).Err(); err != nil {
						t.Fatal(err)
					}
				}
				if strings.HasSuffix(variant, "-retired-url") {
					source = "https://fixture.wd1.myworkdayjobs.com/Careers/retired-link"
					if family == "api_sniffer" {
						source = "https://careers.example.net/retired-link"
					}
				}
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,description_r2_hash=123,next_scrape_at=NULL,is_active=$3 WHERE id=$1::uuid", f.original, source, mode != "inactive"); err != nil {
					t.Fatal(err)
				}
				hash := int64(123)
				if _, err := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: f.board, URL: source, DescriptionHash: &hash, Due: time.Now().Add(-time.Minute), Browser: true}); err != nil {
					t.Fatal(err)
				}
				if strings.HasSuffix(variant, "-retired-step") {
					if err := f.r.HSet(ctx, "scrape:"+f.original, "scrape_step", "9").Err(); err != nil {
						t.Fatal(err)
					}
				}
				var epoch int64
				if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil {
					t.Fatal(err)
				}
				a, err := queue.OpenDefaultAuthority(ctx, f.dsn, f.client, epoch)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(a.Close)
				claim, err := a.Claim(ctx, queue.Browser)
				if err != nil || claim == nil {
					t.Fatal("retired browser claim unavailable", err)
				}
				proxy, skip, err := runtimeClaimTransport(ctx, a, claim)
				if err != nil || proxy {
					t.Fatal("no-fetch canonical transport unavailable", err)
				}
				if err != nil || skip {
					t.Fatal("no-fetch canonical TLS selection unavailable", err)
				}
				if mode == "reactivated" {
					if _, err = f.pg.Exec(ctx, "UPDATE job_posting SET next_scrape_at=now()+interval '1 hour' WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				requests, renders := 0, 0
				client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { requests++; t.Error("retired attempt fetched HTTP") })
				renderer := heldRenderedDetail(func(context.Context, queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
					renders++
					t.Error("retired attempt rendered")
					return nil, nil, nil
				})
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
				if mode == "reactivated" {
					if err == nil || requests != 0 || renders != 0 {
						t.Fatal("reactivated stale lane acquired execution authority", err)
					}
					return
				}
				if err != nil || result == nil || !result.Settled || result.Cycle.Status != "unscheduled" || requests != 0 || renders != 0 {
					t.Fatal("retired lane did not settle without transport", err)
				}
				var title string
				var storedHash *int64
				var active bool
				var due *time.Time
				var failures int
				if err = f.pg.QueryRow(ctx, "SELECT titles[1],description_r2_hash,is_active,next_scrape_at,scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &storedHash, &active, &due, &failures); err != nil {
					t.Fatal(err)
				}
				if title != "Original" || storedHash == nil || *storedHash != 123 || active != (mode != "inactive") || due != nil || failures != 0 {
					t.Fatal("retirement changed canonical content or failure budget")
				}
				if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.Exists(ctx, "scrape:"+f.original).Val() != 0 {
					t.Fatal("retired former lane retained its lease/cache")
				}
			})
		}
	}
}
