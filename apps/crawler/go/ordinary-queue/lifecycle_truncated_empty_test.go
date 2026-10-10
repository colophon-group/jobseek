package queue

import (
	"context"
	"errors"
	"testing"
)

func TestRealTruncatedEmptyInventoryPreservesActivePostingsAndEmptyCounter(t *testing.T) {
	f, authority := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET empty_check_count=3 WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	cycle := beginLifecycle(t, authority)
	result, err := cycle.FinishSuccess(ctx, GreenhouseInventorySummary{Truncated: true})
	if result != nil || !errors.Is(err, ErrConfiguration) {
		t.Fatal("an unproved empty prefix entered authoritative empty handling", err)
	}
	result, err = cycle.FinishFailure(ctx, "inventory completeness could not be established")
	if err != nil || result.Status != "failed" || result.Gone != 0 {
		t.Fatal("ordinary failure settlement unavailable after unproved empty inventory", err)
	}
	var counter int
	var active bool
	if err := f.observer.QueryRow(ctx, "SELECT empty_check_count FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&counter); err != nil {
		t.Fatal(err)
	}
	if err := f.observer.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&active); err != nil {
		t.Fatal(err)
	}
	if counter != 3 || !active {
		t.Fatal("truncated empty inventory changed active state or empty count", counter, active)
	}
	settleLifecycle(t, f, cycle, result)
}
