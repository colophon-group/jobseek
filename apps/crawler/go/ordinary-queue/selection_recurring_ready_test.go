package queue

import (
	"context"
	"fmt"
	"testing"
	"time"
)

func TestRealRecurringDetailReadyOutsideBoardWindow(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, mode := range []string{"ready", "foreign-recurring-head", "foreign-first-time"} {
			t.Run(string(worker)+"/"+mode, func(t *testing.T) {
				p, a := recurringDetailWindowFixture(t, worker)
				ctx := context.Background()
				foreign := "00000000-0000-4000-8000-000000000999"
				if mode != "ready" {
					if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_posting
 (id,company_id,board_id,source_url,titles,locales,last_seen_at,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,'https://foreign.example/job/preserved',
 ARRAY['Foreign posting'],ARRAY['en'],now(),now()-interval '2 minutes')`,
						foreign, p.f.company, p.plan.document.Members[0].BoardID); err != nil {
						t.Fatal(err)
					}
					t.Cleanup(func() {
						_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", foreign)
						// This fixture reuses a fixed ID across subtests. The coverage record
						// intentionally survives posting deletion, so remove fixture data too.
						_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM source_coverage_posting_first WHERE posting_id=$1::uuid", foreign)
					})
					hash := int64(123)
					detail := URLOnlyDetail{ID: foreign, BoardID: p.plan.document.Members[0].BoardID,
						URL: "https://foreign.example/job/preserved", DescriptionHash: &hash,
						Due: time.Now().Add(-2 * time.Minute), Browser: worker == Browser}
					if mode == "foreign-first-time" {
						detail.DescriptionHash = nil
					}
					if _, err := p.f.client.EnqueueURLDetail(ctx, detail); err != nil {
						t.Fatal(err)
					}
				}

				claim, err := a.ClaimDetails(ctx, worker)
				if mode == "foreign-first-time" {
					if err != nil || claim != nil || p.f.client.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 0 {
						t.Fatal("recurring ready observation bypassed global first-time priority", err)
					}
				} else if err != nil || claim == nil || claim.task.ID != p.f.task.ID || claim.task.Kind != Scrape {
					t.Fatal("due recurring detail stranded outside the canonical board window", err)
				} else if p.f.client.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 1 {
					t.Fatal("ready observation changed an unrelated lease")
				}
				if mode != "ready" {
					prefix := "scrapes_"
					if mode == "foreign-first-time" {
						prefix = "ft_scrapes_"
					}
					if _, err := p.f.client.redis.ZScore(ctx, prefix+string(worker)+":foreign.example", foreign).Result(); err != nil {
						t.Fatal("native observation removed foreign work", err)
					}
					var fences int
					if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", foreign).Scan(&fences); err != nil || fences != 0 {
						t.Fatal("native observation adopted a foreign posting", err)
					}
				}
			})
		}
	}
}

func recurringDetailWindowFixture(t *testing.T, worker WorkerType) (firstOwnerFixture, *Authority) {
	t.Helper()
	var p firstOwnerFixture
	if worker == Browser {
		p = firstRenderedDetailFixture(t)
	} else {
		p = firstJSONLDDetailFixture(t)
	}
	ctx := context.Background()
	board := p.plan.document.Details[0].BoardID
	base, err := p.f.client.redis.HGetAll(ctx, "board:"+board).Result()
	if err != nil {
		t.Fatal(err)
	}
	boards := []string{board}
	for i := 0; i < 8; i++ {
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", 200+i)
		config := cloneConfig(base)
		config["board_slug"], config["board_url"] = "recurring-window-"+id, base["board_url"]+"?board="+id
		if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_board
 (id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,
 scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb,60,24,$7,$8,$9)`,
			id, p.f.company, config["board_slug"], config["board_url"], config["crawler_type"],
			config["metadata"], config["throttle_key"], config["monitor_needs_browser"] == "1", worker == Browser); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", id) })
		if err := p.f.client.redis.HSet(ctx, "board:"+id, config).Err(); err != nil {
			t.Fatal(err)
		}
		boards = append(boards, id)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.plan.document.Members[0].BoardID}, boards)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	activateFixturePlan(t, p.f, plan)
	if err := p.f.client.redis.Set(ctx, ownershipProjectionKey, plan.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, plan.digest, plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	if plan.document.Details[8].BoardID != board {
		t.Fatal("due recurring board must be outside the initial eight-board window")
	}
	if err := p.f.client.redis.Del(ctx, "ft_scrapes_"+string(worker)+":"+p.f.task.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZRem(ctx, "ready:"+string(worker)+":0", p.f.task.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	hash := int64(123)
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET description_r2_hash=$2 WHERE id=$1::uuid", p.f.task.ID, hash); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: p.f.task.ID, BoardID: board,
		URL:             "https://jobs.example.net/job/native-" + map[WorkerType]string{Simple: "jsonld", Browser: "rendered"}[worker],
		DescriptionHash: &hash, Due: time.Now().Add(-time.Minute), Browser: worker == Browser}); err != nil {
		t.Fatal(err)
	}
	if p.f.client.redis.ZCard(ctx, "ready:"+string(worker)+":0").Val() != 0 {
		t.Fatal("recurring fixture must have no first-time work")
	}
	if _, err := p.f.client.redis.ZScore(ctx, "ready:"+string(worker)+":2", p.f.task.Domain).Result(); err != nil {
		t.Fatal("canonical recurring detail is absent from the ready index", err)
	}
	p.plan = plan
	return p, a
}
