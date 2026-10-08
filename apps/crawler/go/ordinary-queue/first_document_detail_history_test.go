package queue

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func firstNotionDetailHistoryFixture(t *testing.T) firstOwnerFixture {
	t.Helper()
	p := firstProviderBatchFixture(t, "notion")
	ctx := context.Background()
	source := "https://fixture.notion.site/11111111111111111111111111111111"
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", p.f.task.ID, source); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.Del(ctx, "monitors_simple:notion", "ft_monitors_simple:notion", "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: p.f.task.ID, BoardID: p.f.task.ID, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, []string{p.f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	p.plan = plan
	p.f.task = &Task{ID: p.f.task.ID, Kind: Scrape, Worker: Simple, Domain: "fixture.notion.site"}
	return p
}

func TestRealFirstDocumentDetailHistorySurvivesFreshEpoch(t *testing.T) {
	for _, provider := range []string{"pdf", "notion"} {
		t.Run(provider, func(t *testing.T) {
			var old firstOwnerFixture
			if provider == "pdf" {
				old = firstAPIDetailFixture(t, "pdf")
			} else {
				old = firstNotionDetailHistoryFixture(t)
			}
			a, claim := firstRetirementClaim(t, old)
			ctx := context.Background()
			if _, err := applyFirstFixture(t, old, true); err != nil {
				t.Fatal("document owner retirement", err)
			}
			var before string
			if err := old.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID).Scan(&before); err != nil {
				t.Fatal(err)
			}
			next := firstOwnershipFixtureHistory(t, true)
			if result, err := applyFirstFixture(t, next, false); err != nil || result.State != "active" {
				t.Fatal("legitimate retired document receipt blocked fresh owner", err, result)
			}
			var after string
			if err := old.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID).Scan(&after); err != nil || after != before {
				t.Fatal("immutable document receipt changed", err)
			}
			if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { t.Fatal("retired document writer regained authority"); return nil }); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal(err)
			}
			if _, err := applyFirstFixture(t, next, true); err != nil {
				t.Fatal("fresh owner retirement", err)
			}
		})
	}
}
