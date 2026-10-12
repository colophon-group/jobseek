package queue

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
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

func TestRealOrderedDetailFirstAdoptionPreservesInterruptedLegacyStage(t *testing.T) {
	var cases []struct {
		Slug          string         `json:"slug"`
		BoardURL      string         `json:"board_url"`
		ScraperType   string         `json:"scraper_type"`
		ScraperConfig map[string]any `json:"scraper_config"`
		Expected      bool           `json:"expected"`
	}
	body, err := os.ReadFile("testdata/ordered_detail_configs.json")
	if err != nil || json.Unmarshal(body, &cases) != nil {
		t.Fatal("actual ordered configuration corpus unavailable", err)
	}
	count := 0
	for _, row := range cases {
		if row.Expected {
			count++
			t.Run(row.Slug, func(t *testing.T) {
				metadata, err := json.Marshal(map[string]any{"scraper_type": row.ScraperType, "scraper_config": row.ScraperConfig})
				if err != nil {
					t.Fatal(err)
				}
				source, err := url.Parse(row.BoardURL)
				if err != nil {
					t.Fatal(err)
				}
				p := firstIndependentDetailFixture(t, string(metadata), row.BoardURL, source.Hostname())
				testFirstAdoptionLegacyOrderedStage(t, p)
			})
		}
	}
	if count != 8 {
		t.Fatal("actual configuration coverage incomplete", count)
	}
}

func testFirstAdoptionLegacyOrderedStage(t *testing.T, p firstOwnerFixture) {
	t.Helper()
	ctx := context.Background()
	key := "scrape:" + p.f.task.ID
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, key, "scrape_step", "1", "description_r2_hash", "123", "scrape_interval_hours", "24").Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: inflight(p.f.task)}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "inflight_strikes:simple", inflight(p.f.task), "2").Err(); err != nil {
		t.Fatal(err)
	}
	before := coldCanonicalSnapshot(t, p.f)
	var due time.Time
	if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", p.f.task.ID).Scan(&due); err != nil {
		t.Fatal(err)
	}
	start := seconds(time.Now())
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("expired later legacy stage could not be adopted", err)
	}
	if before != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("adoption changed canonical content, leases or receipts")
	}
	score, err := p.f.client.redis.ZScore(ctx, "ft_scrapes_simple:"+p.f.task.Domain, p.f.task.ID).Result()
	if err != nil || score < start || score > seconds(time.Now()) {
		t.Fatal("interrupted stage lost immediate first queue", err)
	}
	if p.f.client.redis.HGet(ctx, key, "scrape_step").Val() != "1" || p.f.client.redis.HGet(ctx, key, "description_r2_hash").Val() != "123" || p.f.client.redis.HGet(ctx, key, "scrape_interval_hours").Val() != "24" || p.f.client.redis.HGet(ctx, "inflight_strikes:simple", inflight(p.f.task)).Val() != "2" {
		t.Fatal("handoff state or failure history changed")
	}
	if p.f.client.redis.ZScore(ctx, "inflight:simple", inflight(p.f.task)).Err() != redis.Nil {
		t.Fatal("expired lease retained")
	}
	if score, err := p.f.client.redis.ZScore(ctx, "scrapes_simple:"+p.f.task.Domain, p.f.task.ID).Result(); err != nil || score != seconds(due) {
		t.Fatal("canonical future cycle deadline lost", err)
	}
	var attempts int
	if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_write_fence WHERE routing_epoch=$1", p.f.epoch).Scan(&attempts); err != nil || attempts != 0 {
		t.Fatal("adoption invented an attempt", err)
	}
	restartPublicationRedisWithoutSave(t, p.f.client)
	if persisted, err := p.f.client.redis.ZScore(ctx, "ft_scrapes_simple:"+p.f.task.Domain, p.f.task.ID).Result(); err != nil || persisted != score {
		t.Fatal("durable adoption lost pending stage", err)
	}
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("adopted pending stage cannot cold reverse", err)
	}
}

func TestRealOrderedDetailFirstAdoptionRejectsInventedHandoff(t *testing.T) {
	for _, mode := range []string{"advance-step", "replace-hash", "replace-interval", "live-lease"} {
		t.Run(mode, func(t *testing.T) {
			p := firstIndependentDetailFixture(t, `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"steps":[{"tag":"p","field":"description","html":true}]}}}}`, "https://careers.example.net/job/123", "careers.example.net")
			ctx := context.Background()
			task := inflight(p.f.task)
			if err := p.f.client.redis.HSet(ctx, "scrape:"+p.f.task.ID, "scrape_step", "1", "description_r2_hash", "123", "scrape_interval_hours", "24").Err(); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: task}).Err(); err != nil {
				t.Fatal(err)
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			err := WithHostColdSQL(ctx, p.f.observer, hostSQLBindingFixture(), func(ctx context.Context, _ *HostColdSQL) error {
				raw, err := firstRetirementMembers(ctx, p.f.observer, p.f.client, p.plan)
				if err != nil {
					return err
				}
				var rows []firstRetirementMember
				if err := json.Unmarshal([]byte(raw), &rows); err != nil {
					return err
				}
				found := false
				for i := range rows {
					if rows[i].Kind == Scrape {
						found = true
						switch mode {
						case "advance-step":
							v := "2"
							rows[i].NextStep = &v
						case "replace-hash":
							v := "999"
							rows[i].NextHash = &v
						case "replace-interval":
							v := "48"
							rows[i].NextInterval = &v
						case "live-lease":
							if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: seconds(time.Now().Add(time.Hour)), Member: task}).Err(); err != nil {
								return err
							}
							before = snapshot(t, p.f.client)
						}
					}
				}
				if !found {
					t.Fatal("later detail restoration row unavailable")
				}
				body, err := json.Marshal(rows)
				if err != nil {
					return err
				}
				return firstOwnershipProjection(ctx, p.f.client, p.f.epoch, p.plan, p.target, "publish", string(body))
			})
			if !errors.Is(err, ErrAuthorityLost) || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
				t.Fatal("invalid handoff passed or partially mutated authority", err)
			}
		})
	}
}
