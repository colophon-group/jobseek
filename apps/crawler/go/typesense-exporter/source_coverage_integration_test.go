package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestSourceCoverageMigrationRetainsClosedOriginalsAndResetsRevisions(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	conn, dsn := migrationFixtureConnection(t, ctx, "jobseek_source_coverage_test")
	if _, err := migrateLocal(ctx, conn); err != nil {
		t.Fatal(err)
	}
	_, err := conn.Exec(ctx, `INSERT INTO public.company(id,slug,name,website) VALUES
('00000000-0000-0000-0000-000000000101','coverage-test','Coverage Test','https://example.com');
INSERT INTO public.job_board(id,company_id,board_url,crawler_type,metadata)
VALUES ('00000000-0000-0000-0000-000000000102','00000000-0000-0000-0000-000000000101','https://example.com/jobs','rss','{"_monitor_config_fingerprint":"stable"}');
INSERT INTO public.job_posting(id,company_id,board_id,source_url,first_seen_at)
VALUES ('00000000-0000-0000-0000-000000000103','00000000-0000-0000-0000-000000000101','00000000-0000-0000-0000-000000000102','https://example.com/jobs/1','2026-11-01T10:00:00Z');
UPDATE public.job_posting SET is_active=false WHERE id='00000000-0000-0000-0000-000000000103'`)
	if err != nil {
		t.Fatal(err)
	}
	var firstRevision, postingRevision string
	var first time.Time
	var active bool
	err = conn.QueryRow(ctx, `SELECT s.revision::text,o.revision::text,o.first_observed_at,p.is_active
FROM public.source_coverage_scope s JOIN public.source_coverage_posting_first o ON o.board_id=s.board_id
JOIN public.job_posting p ON p.id=o.posting_id WHERE s.board_id='00000000-0000-0000-0000-000000000102'`).Scan(&firstRevision, &postingRevision, &first, &active)
	if err != nil || firstRevision != postingRevision || active || !first.Equal(time.Date(2026, 11, 1, 10, 0, 0, 0, time.UTC)) {
		t.Fatalf("closed first observation: revision %q/%q first %v active %v error %v", firstRevision, postingRevision, first, active, err)
	}
	reader, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close(ctx)
	snapshot, err := reader.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		t.Fatal(err)
	}
	defer snapshot.Rollback(ctx)
	var before, after int
	if err := snapshot.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_posting_first`).Scan(&before); err != nil {
		t.Fatal(err)
	}
	_, err = conn.Exec(ctx, `INSERT INTO public.job_posting(id,company_id,board_id,source_url,first_seen_at)
VALUES ('00000000-0000-0000-0000-000000000104','00000000-0000-0000-0000-000000000101','00000000-0000-0000-0000-000000000102','https://example.com/jobs/2','2026-11-01T11:00:00Z')`)
	if err != nil {
		t.Fatal(err)
	}
	if err := snapshot.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_posting_first`).Scan(&after); err != nil || before != after {
		t.Fatalf("concurrent insert changed read-only snapshot: %d %d %v", before, after, err)
	}
	if err := snapshot.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	_, err = conn.Exec(ctx, `UPDATE public.job_board SET last_checked_at=now(),metadata=metadata||'{"runtime_receipt":"ok"}'::jsonb WHERE id='00000000-0000-0000-0000-000000000102'`)
	if err != nil {
		t.Fatal(err)
	}
	var runtimeRevision string
	err = conn.QueryRow(ctx, `SELECT revision::text FROM public.source_coverage_scope WHERE board_id='00000000-0000-0000-0000-000000000102'`).Scan(&runtimeRevision)
	if err != nil || runtimeRevision != firstRevision {
		t.Fatalf("runtime metadata reset revision: %v", err)
	}
	_, err = conn.Exec(ctx, `UPDATE public.job_board SET board_url='https://example.com/careers' WHERE id='00000000-0000-0000-0000-000000000102'`)
	if err != nil {
		t.Fatal(err)
	}
	var changedRevision string
	err = conn.QueryRow(ctx, `SELECT revision::text FROM public.source_coverage_scope WHERE board_id='00000000-0000-0000-0000-000000000102'`).Scan(&changedRevision)
	if err != nil || changedRevision == firstRevision {
		t.Fatalf("source URL change did not reset revision: %v", err)
	}
	projection, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		t.Fatal(err)
	}
	periodEnd := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	view, err := buildCoverageSnapshot(ctx, projection, periodEnd, periodEnd.Add(time.Hour), []string{"coverage-test"})
	if err != nil {
		projection.Rollback(ctx)
		t.Fatal(err)
	}
	if err := projection.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if len(view.Companies) != 1 || len(view.Scopes) != 1 || len(view.Postings) != 2 || view.Scopes[0].Coverage[0].State != "unknown" || view.Scopes[0].LastVerifiedAt != nil {
		t.Fatalf("incomplete first-observation projection: %+v", view)
	}
	if _, err := conn.Exec(ctx, `DELETE FROM public.source_coverage_scope WHERE board_id='00000000-0000-0000-0000-000000000102'`); err != nil {
		t.Fatal(err)
	}
	missing, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		t.Fatal(err)
	}
	_, err = buildCoverageSnapshot(ctx, missing, periodEnd, periodEnd.Add(time.Hour), []string{"coverage-test"})
	missing.Rollback(ctx)
	if err == nil {
		t.Fatal("approved board without scope silently omitted")
	}
	python, err := filepath.Abs(filepath.Join("../..", ".venv/bin/python"))
	if err != nil {
		t.Fatal(err)
	}
	command := exec.CommandContext(ctx, python, "-m", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0041")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn, "CRAWLER_DB_ROLE=fixture-migrate")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("coverage schema downgrade: %s", output)
	}
	var exists *string
	err = conn.QueryRow(ctx, `SELECT to_regclass('public.source_coverage_scope')::text`).Scan(&exists)
	if err != nil || exists != nil {
		t.Fatalf("coverage schema remained after explicit downgrade: %v %v", exists, err)
	}
}
