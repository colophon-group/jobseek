package queue

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestRealOrderedDetailActualConfigurationsColdRetirement(t *testing.T) {
	var input []struct {
		Slug          string         `json:"slug"`
		BoardURL      string         `json:"board_url"`
		ScraperType   string         `json:"scraper_type"`
		ScraperConfig map[string]any `json:"scraper_config"`
		Expected      bool           `json:"expected"`
	}
	b, err := os.ReadFile("testdata/ordered_detail_configs.json")
	if err != nil || json.Unmarshal(b, &input) != nil {
		t.Fatal("configuration projection invalid", err)
	}
	rows := input[:0]
	for _, row := range input {
		if row.Expected {
			rows = append(rows, row)
		}
	}
	if len(rows) != 8 {
		t.Fatal("complete static chain set unavailable")
	}
	for _, row := range rows {
		t.Run(row.Slug, func(t *testing.T) {
			testDetailFixtureRetirement(t, func(t *testing.T) firstOwnerFixture {
				metadata, _ := json.Marshal(map[string]any{"scraper_type": row.ScraperType, "scraper_config": row.ScraperConfig})
				u, err := url.Parse(row.BoardURL)
				if err != nil {
					t.Fatal(err)
				}
				return firstIndependentDetailFixture(t, string(metadata), row.BoardURL, u.Hostname())
			})
		})
	}
}

func TestRealOrderedDetailColdRetirementPublishesUnacknowledgedStep(t *testing.T) {
	for _, mode := range []string{"unpublished", "published", "active-later"} {
		t.Run(mode, func(t *testing.T) {
			p := firstIndependentDetailFixture(t, `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"steps":[{"tag":"p","field":"description","html":true}]}}}}`, "https://careers.example.net/job/123", "careers.example.net")
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
					next, err := a.Claim(ctx, Simple)
					if err != nil || next == nil || next.task.Config["scrape_step"] != "1" {
						t.Fatal("later attempt missing", err)
					}
				}
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("original cold reversal failed", err)
			}
			score, err := p.f.client.redis.ZScore(ctx, "ft_scrapes_simple:careers.example.net", claim.task.ID).Result()
			if err != nil || p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "scrape_step").Val() != "1" || mode != "active-later" && score != seconds(at) || mode == "active-later" && (score < seconds(at) || score > seconds(time.Now())) {
				t.Fatal("cold reversal lost pending independent stage", err)
			}
			if p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "description_r2_hash").Val() != "123" || p.f.client.redis.HGet(ctx, "scrape:"+claim.task.ID, "scrape_interval_hours").Val() != "24" {
				t.Fatal("cold reversal lost handoff metadata")
			}
		})
	}
}
