package queue

import (
	"context"
	"errors"
	"testing"
	"time"
)

// A blocked board must retain its own canonical lock without consuming every
// other monitor's transaction budget while they wait for the sole connection.
func TestRealAuthorityBlockedBoardDoesNotStarveIndependentBoard(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	other := ordinaryID(t)
	otherURL := "https://job-boards.greenhouse.io/independent-" + other
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_board
 (id,company_id,board_slug,board_url,crawler_type,metadata,
 check_interval_minutes,scrape_interval_hours,throttle_key,
 monitor_needs_browser,scraper_needs_browser,is_enabled,board_status)
 SELECT $2::uuid,company_id,$3,$4,crawler_type,metadata,
 check_interval_minutes,scrape_interval_hours,throttle_key,
 monitor_needs_browser,scraper_needs_browser,is_enabled,board_status
 FROM job_board WHERE id=$1::uuid`, f.task.ID, other, "independent-"+other, otherURL); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", other)
	})
	config, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	config["board_slug"] = "independent-" + other
	config["board_url"] = otherURL
	if err := f.client.redis.HSet(ctx, "board:"+other, config).Err(); err != nil {
		t.Fatal(err)
	}
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
		var waiting bool
		if err := f.observer.QueryRow(ctx, `SELECT EXISTS(
 SELECT 1 FROM pg_stat_activity
 WHERE application_name='jobseek:crawler:ordinary-authority:local'
 AND $1::integer=ANY(pg_blocking_pids(pid)))`, pid).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-done:
			t.Fatalf("canonical lock was bypassed: %v", err)
		case <-ctx.Done():
			t.Fatal("canonical blocking was not observed")
		case <-time.After(5 * time.Millisecond):
		}
	}
	independent, stop := context.WithTimeout(ctx, time.Second)
	defer stop()
	profile, err := f.authority.ObserveGreenhouseMonitor(independent, other)
	if err != nil || profile.BoardID != other {
		t.Fatalf("independent board starved behind unrelated canonical lock: %v", err)
	}
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if !errors.Is(err, ErrUnsupportedProfile) {
			t.Fatalf("blocked board admitted after canonical disable: %v", err)
		}
	case <-ctx.Done():
		t.Fatal("blocked observation did not finish after canonical change")
	}
}
