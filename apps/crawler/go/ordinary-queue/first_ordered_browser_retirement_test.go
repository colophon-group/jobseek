package queue

import (
	"context"
	"github.com/jackc/pgx/v5"
	"testing"
	"time"
)

func firstMixedOrderedBrowserFixture(t *testing.T) firstOwnerFixture {
	return firstIndependentDetailFixture(t, `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"steps":[{"tag":"p","field":"description","html":true}]}}}}`, "https://careers.example.net/job/123", "careers.example.net", Browser)
}
func TestRealOrderedBrowserDetailColdRetirementRestoresOriginalLane(t *testing.T) {
	testIndependentDetailColdRetirement(t, firstMixedOrderedBrowserFixture)
}
func TestRealOrderedBrowserDetailFirstAdoptionPreservesInterruptedLegacyStage(t *testing.T) {
	testFirstAdoptionLegacyOrderedStage(t, firstMixedOrderedBrowserFixture(t))
}
func TestRealOrderedBrowserDetailColdRetirementPublishesUnacknowledgedStep(t *testing.T) {
	for _, mode := range []string{"unpublished", "published", "active-later"} {
		t.Run(mode, func(t *testing.T) {
			p := firstMixedOrderedBrowserFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			detail, err := a.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			receipt, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
				return err
			})
			if err != nil {
				t.Fatal(err)
			}
			var at time.Time
			if err := p.f.observer.QueryRow(ctx, "SELECT next_detail_at FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID).Scan(&at); err != nil {
				t.Fatal(err)
			}
			if mode != "unpublished" {
				if err := a.Settle(ctx, claim, receipt); err != nil {
					t.Fatal(err)
				}
				if mode == "active-later" {
					next, err := a.Claim(ctx, Browser)
					if err != nil || next == nil || next.task.Config["scrape_step"] != "1" {
						t.Fatal("later attempt missing", err)
					}
				}
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("original cold reversal failed", err)
			}
			score, err := p.f.client.redis.ZScore(ctx, "ft_scrapes_browser:careers.example.net", claim.task.ID).Result()
			if err != nil || p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "scrape_step").Val() != "1" || mode != "active-later" && score != seconds(at) || mode == "active-later" && (score < seconds(at) || score > seconds(time.Now())) {
				t.Fatal("cold reversal lost pending independent stage", err)
			}
			if p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "description_r2_hash").Val() != "123" || p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "scrape_interval_hours").Val() != "24" {
				t.Fatal("cold reversal lost handoff metadata")
			}
		})
	}
}
