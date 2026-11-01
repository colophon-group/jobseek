package queue

import (
	"context"
	"testing"
)

func TestSourceCoverageOutcomeRequiresFullInventory(t *testing.T) {
	base := GreenhouseInventorySummary{Discovered: 2}
	if got := sourceCoverageOutcome(base, 2, 2, ""); got != "complete" {
		t.Fatalf("full inventory: %s", got)
	}
	truncated := base
	truncated.Truncated = true
	if got := sourceCoverageOutcome(truncated, 2, 2, ""); got != "partial" {
		t.Fatalf("truncated inventory: %s", got)
	}
	filtered := base
	filtered.ProcessingFiltered = 1
	if got := sourceCoverageOutcome(filtered, 1, 1, ""); got != "partial" {
		t.Fatalf("filtered inventory: %s", got)
	}
	if got := sourceCoverageOutcome(base, 1, 1, ""); got != "partial" {
		t.Fatalf("incomplete processing: %s", got)
	}
	if got := sourceCoverageOutcome(base, 2, 1, ""); got != "partial" {
		t.Fatalf("duplicate inventory: %s", got)
	}
	if got := sourceCoverageOutcome(base, 2, 2, "unverified"); got != "partial" {
		t.Fatalf("unverified disappearance: %s", got)
	}
}

func TestRealSourceCoverageTerminalReceipts(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	complete := beginLifecycle(t, a)
	result, err := complete.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 0})
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, complete, result)
	var count int
	var outcome string
	if err := f.observer.QueryRow(ctx, `SELECT count(*),min(outcome) FROM public.source_coverage_check WHERE board_id=$1::uuid`, f.task.ID).Scan(&count, &outcome); err != nil || count != 1 || outcome != "complete" {
		t.Fatalf("complete receipt %d %s %v", count, outcome, err)
	}
	dueLifecycle(t, f)
	partial := beginLifecycle(t, a)
	result, err = partial.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 2, ProcessingFiltered: 2})
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, partial, result)
	if err := f.observer.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_check WHERE board_id=$1::uuid AND outcome='partial'`, f.task.ID).Scan(&count); err != nil || count != 1 {
		t.Fatalf("partial receipt %d %v", count, err)
	}
	dueLifecycle(t, f)
	failed := beginLifecycle(t, a)
	result, err = failed.FinishFailure(ctx, "bounded source failure")
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, failed, result)
	if err := f.observer.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_check WHERE board_id=$1::uuid AND outcome='failed'`, f.task.ID).Scan(&count); err != nil || count != 1 {
		t.Fatalf("failed receipt %d %v", count, err)
	}
}
