package queue

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type observedSelectionTx struct {
	pgx.Tx
	queries int
}

func (tx *observedSelectionTx) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	tx.queries++
	return tx.Tx.QueryRow(ctx, sql, args...)
}

func (tx *observedSelectionTx) Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error) {
	tx.queries++
	return tx.Tx.Query(ctx, sql, args...)
}

// Dense queues must not spend one canonical SQL round trip per losing posting
// while holding the shared lease barrier. The chosen posting still requires its
// exclusive lock and exact canonical source check immediately before the pop.
func TestRealDenseDetailCandidateObservationSQLBudget(t *testing.T) {
	f := currentWorkdayDetailQueueFixture(t)
	ctx := context.Background()
	ids := make([]string, ownershipCandidateBatch-1)
	for i := range ids {
		ids[i] = ordinaryID(t)
	}
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at)
 SELECT n.id, p.company_id,p.board_id,p.source_url||n.id::text,p.titles,p.locales,now()-interval '1 minute'
 FROM job_posting p CROSS JOIN unnest($2::uuid[]) AS n(id) WHERE p.id=$1::uuid`, f.task.ID, ids); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=ANY($1::uuid[])", ids)
	})
	hash := int64(123)
	for _, id := range ids {
		if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: f.task.ID, URL: "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001" + id, DescriptionHash: &hash, Due: time.Now().Add(-time.Minute)}); err != nil {
			t.Fatal(err)
		}
	}
	plan, err := f.authority.StageOwnership(ctx, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	activateFixturePlan(t, f, plan)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.digest)
	})
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, plan.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, plan.digest, plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	var candidates []ownershipCandidate
	queries := 0
	started := time.Now()
	err = a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		observed := &observedSelectionTx{Tx: tx}
		var err error
		candidates, err = a.detailCandidates(ctx, observed, seconds(time.Now()), Simple)
		queries = observed.queries
		return err
	})
	if err != nil || len(candidates) != ownershipCandidateBatch {
		t.Fatalf("dense candidate inventory changed: count=%d error=%v", len(candidates), err)
	}
	t.Logf("candidates=%d SQL_queries=%d elapsed=%s", len(candidates), queries, time.Since(started))
	if queries > 8 {
		t.Fatal("candidate observation serialized posting SQL while holding the lease barrier")
	}
	if f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
		t.Fatal("observation granted a posting lease")
	}
}
