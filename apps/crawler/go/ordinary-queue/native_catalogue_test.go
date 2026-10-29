package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/redis/go-redis/v9"
)

func TestDefaultAuthorityAcceptsNewBoardWithoutMigrationPlan(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	before := snapshot(t, f.client)
	a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
	if err != nil {
		t.Fatal("default startup rejected qualified catalogue", err)
	}
	defer a.Close()
	if !reflect.DeepEqual(before, snapshot(t, f.client)) || !a.RequiresRenderedDetails() || !a.RequiresProxyHTTP() {
		t.Fatal("default startup mutated queues or omitted required transports")
	}
	id := ordinaryID(t)
	slug := "new-default-fixture"
	url := "https://job-boards.greenhouse.io/new-default-fixture"
	metadata := `{"token":"new-default-fixture","scraper_type":"skip"}`
	_, err = f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,
 check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser,next_check_at)
 SELECT $1::uuid,company_id,$2,$3,crawler_type,$4::jsonb,check_interval_minutes,scrape_interval_hours,
 throttle_key,monitor_needs_browser,scraper_needs_browser,clock_timestamp()-interval '1 minute'
 FROM job_board WHERE id=$5::uuid`, id, slug, url, metadata, f.task.ID)
	if err != nil {
		t.Fatal("new canonical fixture board failed")
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", id)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", id)
	})
	config, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	config["board_slug"], config["board_url"], config["metadata"] = slug, url, metadata
	if err = f.client.redis.HSet(ctx, "board:"+id, config).Err(); err != nil {
		t.Fatal(err)
	}
	now, err := f.client.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err = f.client.redis.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: now - 1, Member: id}).Err(); err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for range 2 {
		claim, err := a.Claim(ctx, Simple)
		if err != nil || claim == nil || !claim.task.Fenced() {
			t.Fatal("default canonical claim failed", err)
		}
		seen[claim.boardID] = true
		if _, err := a.ObserveGreenhouseMonitor(ctx, claim.boardID); err != nil {
			t.Fatal("claimed board lost canonical eligibility")
		}
		if err := a.Heartbeat(ctx, claim); err != nil {
			t.Fatal("default fenced heartbeat failed")
		}
	}
	if !seen[id] || !seen[f.task.ID] {
		t.Fatal("new board required a migration membership update")
	}
	var plans int
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&plans); err != nil || plans != 0 {
		t.Fatal("default claims created migration ownership")
	}
}

func TestDefaultAuthorityRejectsMixedOwnersWithoutQueueEffects(t *testing.T) {
	for _, kind := range []string{"SQL-owner", "projection", "B0", "unsupported-board", "stale-cache"} {
		t.Run(kind, func(t *testing.T) {
			var f authorityFixture
			if kind == "SQL-owner" {
				f, _, _ = realOwnedAuthority(t)
			} else {
				f = greenhouseAuthorityFixture(t)
			}
			ctx := context.Background()
			switch kind {
			case "projection":
				if err := f.client.redis.Set(ctx, ownershipProjectionKey, "fixture", 0).Err(); err != nil {
					t.Fatal(err)
				}
			case "B0":
				if err := f.client.redis.HSet(ctx, "lightpanda-b0:legacy-guard", f.task.ID, "fixture").Err(); err != nil {
					t.Fatal(err)
				}
			case "unsupported-board":
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET crawler_type='unknown-native-fixture' WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "stale-cache":
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Err(); err != nil {
					t.Fatal(err)
				}
			}
			before := snapshot(t, f.client)
			if a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch); err == nil {
				a.Close()
				t.Fatal("default startup adopted incompatible authority")
			}
			if !reflect.DeepEqual(before, snapshot(t, f.client)) {
				t.Fatal("rejected startup changed queue state")
			}
		})
	}
}

func TestDefaultAuthorityLosesAuthorityWhenOldGuardReturns(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal("default claim failed")
	}
	if err := f.client.redis.HSet(ctx, "lightpanda-b0:legacy-guard", f.task.ID, "fixture").Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, f.client)
	if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("default heartbeat bypassed returning B0 guard")
	}
	if _, err := a.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("default claim bypassed returning B0 guard")
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("lost default authority changed queue state")
	}
}
