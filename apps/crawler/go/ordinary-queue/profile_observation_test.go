package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func greenhouseAuthorityFixture(t *testing.T) authorityFixture {
	t.Helper()
	f := realAuthority(t, Monitor, Simple)
	config := profileConfig()
	config["company_id"] = f.company
	config["board_slug"] = "ordinary-" + f.company
	ctx := context.Background()
	_, err := f.observer.Exec(ctx, `UPDATE job_board SET board_url=$2,metadata=$3::jsonb,
   check_interval_minutes=60,scrape_interval_hours=24,throttle_key='greenhouse',
   monitor_needs_browser=false,scraper_needs_browser=false,is_enabled=true,board_status='active'
   WHERE id=$1::uuid`, f.task.ID, config["board_url"], config["metadata"])
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.Del(ctx, "board:"+f.task.ID).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); err != nil {
		t.Fatal(err)
	}
	// Match the fixture's ready queue to the effective provider throttle route.
	if err := f.client.redis.Rename(ctx, "monitors_simple:"+f.task.Domain, "monitors_simple:greenhouse").Err(); err != nil {
		t.Fatal(err)
	}
	readyScore, err := f.client.redis.ZScore(ctx, "ready:simple:1", f.task.Domain).Result()
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZRem(ctx, "ready:simple:1", f.task.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: readyScore, Member: "greenhouse"}).Err(); err != nil {
		t.Fatal(err)
	}
	f.task.Domain = "greenhouse"
	return f
}

func TestRealAuthorityGreenhouseDelayedObservationRejectsDisable(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	blocker, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	var pid int
	if err := blocker.QueryRow(ctx, "SELECT pg_backend_pid()").Scan(&pid); err != nil {
		t.Fatal(err)
	}
	if _, err := blocker.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := f.authority.ObserveGreenhouseMonitor(ctx, f.task.ID)
		done <- err
	}()
	for {
		select {
		case err := <-done:
			t.Fatalf("observation did not wait for canonical row authority: %v", err)
		default:
		}
		var waiting bool
		if err := f.observer.QueryRow(ctx, `SELECT EXISTS(
  SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:ordinary-authority:local'
  AND $1::integer=ANY(pg_blocking_pids(pid)))`, pid).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("observation never reached canonical row lock")
		}
		time.Sleep(5 * time.Millisecond)
	}
	before := snapshot(t, f.client)
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err := awaitWrite(t, done); !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatal("delayed observation admitted the now-disabled canonical board")
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("delayed rejection mutated queue state")
	}
}

func TestRealAuthorityGreenhouseObservationWithoutClaimEffects(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	before := snapshot(t, f.client)
	profile, err := f.authority.ObserveGreenhouseMonitor(ctx, f.task.ID)
	if err != nil || profile.BoardID != f.task.ID || profile.CompanyID != f.company || profile.Token != "fixture" || profile.Domain != "greenhouse" {
		t.Fatalf("canonical profile observation failed: %v", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("profile observation changed queue state")
	}
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&count); err != nil || count != 0 {
		t.Fatal("observation activated a retained write fence")
	}
	// Lifecycle observations may legitimately reach caches at a different time
	// from canonical config publication. They remain bound to the exact snapshot.
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"token":"fixture","scraper_type":"skip","suspect_streak":2}`, "egress_host", "boards-api.greenhouse.io").Err(); err != nil {
		t.Fatal(err)
	}
	changed, err := f.authority.ObserveGreenhouseMonitor(ctx, f.task.ID)
	if err != nil || changed.EffectiveConfigSHA256 != profile.EffectiveConfigSHA256 || changed.SnapshotSHA256 == profile.SnapshotSHA256 {
		t.Fatal("runtime observation drift lost either effective or exact binding")
	}
}

func TestRealAuthorityGreenhouseObservationRejectsRetiredAndStaleState(t *testing.T) {
	for _, mode := range []string{"disabled", "canonical_token", "cached_token", "company", "interval", "throttle", "unsupported", "missing_cache", "retired_epoch"} {
		t.Run(mode, func(t *testing.T) {
			f := greenhouseAuthorityFixture(t)
			ctx := context.Background()
			want := ErrAuthorityLost
			var err error
			switch mode {
			case "disabled":
				_, err = f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID)
				want = ErrUnsupportedProfile
			case "canonical_token":
				_, err = f.observer.Exec(ctx, `UPDATE job_board SET metadata='{"token":"changed","scraper_type":"skip"}'::jsonb WHERE id=$1::uuid`, f.task.ID)
			case "cached_token":
				err = f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Err()
			case "company":
				err = f.client.redis.HSet(ctx, "board:"+f.task.ID, "company_id", "00000000-0000-4000-8000-000000000099").Err()
			case "interval":
				err = f.client.redis.HSet(ctx, "board:"+f.task.ID, "check_interval_minutes", "120").Err()
			case "throttle":
				err = f.client.redis.HSet(ctx, "board:"+f.task.ID, "domain", "changed", "throttle_key", "changed").Err()
			case "unsupported":
				_, err = f.observer.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"proxy":"secret"}'::jsonb WHERE id=$1::uuid`, f.task.ID)
				want = ErrUnsupportedProfile
			case "missing_cache":
				err = f.client.redis.Del(ctx, "board:"+f.task.ID).Err()
				want = ErrUnsupportedProfile
			case "retired_epoch":
				_, err = f.observer.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
			}
			if err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, f.client)
			profile, err := f.authority.ObserveGreenhouseMonitor(ctx, f.task.ID)
			if !errors.Is(err, want) || profile != (GreenhouseMonitorProfile{}) {
				t.Fatalf("stale or unsupported canonical profile admitted: %v", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, f.client)) {
				t.Fatal("rejected observation changed queue state")
			}
		})
	}
}
