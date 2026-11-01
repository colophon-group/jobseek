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

func TestRealSourceCoverageCannotCreditUnfingerprintedConfigChange(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	cycle := beginLifecycle(t, a)
	if _, err := f.observer.Exec(ctx, `UPDATE public.job_board SET metadata=metadata||'{"token":"changed"}'::jsonb WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	var revision string
	if err := f.observer.QueryRow(ctx, `SELECT revision::text FROM public.source_coverage_scope WHERE board_id=$1::uuid`, f.task.ID).Scan(&revision); err != nil || revision != cycle.scopeRevision {
		t.Fatalf("test did not isolate effective config drift: %s %v", revision, err)
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
		t.Fatalf("changed monitor config credited complete coverage: %q %v", outcome, err)
	}
}

func TestRealSourceCoverageStableChangeResetsScopeBetweenCompleteChecks(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	first := beginLifecycle(t, a)
	result, err := first.FinishSuccess(ctx, GreenhouseInventorySummary{})
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, first, result)
	oldRevision := first.scopeRevision
	if _, err := f.observer.Exec(ctx, `UPDATE public.job_board SET metadata=metadata||'{"token":"changed"}'::jsonb WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	var unchanged string
	if err := f.observer.QueryRow(ctx, `SELECT revision::text FROM public.source_coverage_scope WHERE board_id=$1::uuid`, f.task.ID).Scan(&unchanged); err != nil || unchanged != oldRevision {
		t.Fatalf("test did not isolate stable config drift: %s %v", unchanged, err)
	}
	tx, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	canonical, err := readCanonicalBoardConfig(ctx, tx, f.task.ID, true)
	if err != nil {
		t.Fatal(err)
	}
	fresh, err := InspectRichMonitor(f.task.ID, canonical)
	if err != nil {
		t.Fatal(err)
	}
	secondTask := first.claim.task
	secondTask.claimToken = ordinaryID(t)
	secondTask.Config = make(map[string]string, len(first.claim.task.Config))
	for key, value := range first.claim.task.Config {
		secondTask.Config[key] = value
	}
	secondTask.Config["metadata"] = canonical["metadata"]
	second := &GreenhouseCycle{claim: &Claim{task: secondTask}}
	tracked, err := second.bindSourceCoverageScope(ctx, tx, fresh.EffectiveConfigSHA256)
	if err != nil {
		t.Fatal(err)
	}
	if second.scopeRevision == oldRevision {
		t.Fatal("effective config change retained old scope revision")
	}
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&second.startedAt); err != nil || second.startedAt.Before(tracked) {
		t.Fatalf("new cycle started before its revision: %v", err)
	}
	if err := second.recordSourceCoverage(ctx, tx, "complete"); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	var completed, partial, distinctRevisions int
	if err := f.observer.QueryRow(ctx, `SELECT count(*) FILTER(WHERE outcome='complete'),count(*) FILTER(WHERE outcome='partial'),count(DISTINCT revision)
FROM public.source_coverage_check WHERE board_id=$1::uuid`, f.task.ID).Scan(&completed, &partial, &distinctRevisions); err != nil || completed != 2 || partial != 0 || distinctRevisions != 2 {
		t.Fatalf("stable change continuity: complete=%d partial=%d revisions=%d error=%v", completed, partial, distinctRevisions, err)
	}
}
