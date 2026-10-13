package queue

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestRealFirstDetailRetirementPreservesAllReceiptsAcrossPages(t *testing.T) {
	p := firstWorkdayDetailFixture(t)
	a, claim := firstRetirementClaim(t, p)
	ctx := context.Background()
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	var due time.Time
	if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", claim.task.ID).Scan(&due); err != nil {
		t.Fatal(err)
	}
	ids := []string{claim.task.ID}
	for i := 0; i < 512; i++ {
		id := ordinaryID(t)
		if i == 0 {
			// The first page has no lower UUID sentinel that can omit a
			// legitimate retained receipt at the zero UUID.
			id = "00000000-0000-0000-0000-000000000000"
		}
		ids = append(ids, id)
		if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at)
 SELECT $2::uuid,company_id,board_id,source_url||$2::text,titles,locales,next_scrape_at
 FROM job_posting WHERE id=$1::uuid`, claim.task.ID, id); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", id)
			_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
		})
		var source string
		if err := p.f.observer.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE id=$1::uuid", id).Scan(&source); err != nil {
			t.Fatal(err)
		}
		if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: claim.boardID, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
			t.Fatal(err)
		}
		config, err := p.f.client.redis.HGetAll(ctx, "scrape:"+id).Result()
		if err != nil {
			t.Fatal(err)
		}
		// Fixture history represents independent completed-before-ACK
		// attempts. Retirement may restore queues but must preserve every
		// canonical row and retained receipt without making new writes.
		if _, err := p.f.observer.Exec(ctx, `INSERT INTO ordinary_worker_write_fence
 (task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state,next_due_at)
 SELECT task_kind,$2::uuid,board_id,routing_epoch,claim_token,$3,state,next_due_at
 FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid`, claim.task.ID, id, configDigest(config)); err != nil {
			t.Fatal(err)
		}
	}
	before := coldCanonicalSnapshot(t, p.f)
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("multi-page canonical retirement failed", err)
	}
	if before != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("paged retirement rewrote canonical content or retained receipts")
	}
	for _, id := range ids {
		score, err := p.f.client.redis.ZScore(ctx, "scrapes_simple:"+claim.task.Domain, id).Result()
		if err != nil || score != seconds(due) {
			t.Fatal("page boundary lost a canonical deadline", err)
		}
	}
	if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || p.f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
		t.Fatal("paged retirement retained a native lease or token")
	}
}
