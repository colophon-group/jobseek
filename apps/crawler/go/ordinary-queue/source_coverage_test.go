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

func TestRealSourceCoverageCannotCreditChangedScope(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	cycle := beginLifecycle(t, a)
	var oldRevision string
	if err := f.observer.QueryRow(ctx, `SELECT revision::text FROM public.source_coverage_scope WHERE board_id=$1::uuid`, f.task.ID).Scan(&oldRevision); err != nil {
		t.Fatal(err)
	}
	if oldRevision != cycle.scopeRevision {
		t.Fatal("cycle did not capture scope revision at start")
	}
	if _, err := f.observer.Exec(ctx, `UPDATE public.job_board SET board_url='https://changed.example.org/careers' WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	tx, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err := cycle.recordSourceCoverage(ctx, tx, "complete"); err != nil {
		t.Fatal(err)
	}
	var outcome string
	if err := tx.QueryRow(ctx, `SELECT outcome FROM public.source_coverage_check WHERE board_id=$1::uuid`, f.task.ID).Scan(&outcome); err != nil || outcome != "partial" {
		t.Fatalf("old cycle credited new scope: %q %v", outcome, err)
	}
}

func TestRealSourceCoverageRejectsStaleConfigAtCycleStart(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatalf("claim: %v", err)
	}
	if _, err := f.observer.Exec(ctx, `UPDATE public.job_board SET board_url='https://changed.example.org/careers' WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := a.BeginGreenhouseCycle(ctx, claim); err == nil {
		t.Fatal("stale monitor config admitted coverage cycle")
	}
	var count int
	if err := f.observer.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_check WHERE board_id=$1::uuid`, f.task.ID).Scan(&count); err != nil || count != 0 {
		t.Fatalf("stale claim wrote coverage: %d %v", count, err)
	}
}
