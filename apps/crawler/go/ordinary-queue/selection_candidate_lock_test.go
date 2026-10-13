package queue

import (
	"context"
	"testing"
	"time"
)

// A posting lock belonging to an unrelated writer must not serialize candidate
// discovery or grant a lease against an unlocked, stale canonical observation.
func TestRealDetailSelectionLocksOnlyAvailableChosenPosting(t *testing.T) {
	for _, firstTime := range []bool{false, true} {
		for _, mode := range []string{"later_locked", "oldest_locked", "both_locked"} {
			name := "recurring/" + mode
			if firstTime {
				name = "first_time/" + mode
			}
			t.Run(name, func(t *testing.T) {
				f := currentWorkdayDetailQueueFixture(t)
				ctx := context.Background()
				id := ordinaryID(t)
				if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at)
 SELECT $2::uuid,company_id,board_id,source_url||$2::text,titles,locales,now()-interval '30 seconds'
 FROM job_posting WHERE id=$1::uuid`, f.task.ID, id); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", id)
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
				})
				var source string
				if err := f.observer.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE id=$1::uuid", id).Scan(&source); err != nil {
					t.Fatal(err)
				}
				hash := int64(123)
				description := &hash
				if firstTime {
					description = nil
					if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET description_r2_hash=NULL WHERE id=$1::uuid OR id=$2::uuid", f.task.ID, id); err != nil {
						t.Fatal(err)
					}
					if err := f.client.redis.ZRem(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", f.task.ID).Err(); err != nil {
						t.Fatal(err)
					}
					if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: f.task.ID, BoardID: f.task.ID, URL: "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001", Due: time.Now().Add(-time.Minute)}); err != nil {
						t.Fatal(err)
					}
				}
				if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: f.task.ID, URL: source, DescriptionHash: description, Due: time.Now().Add(-30 * time.Second)}); err != nil {
					t.Fatal(err)
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
				blocker, err := f.observer.Begin(ctx)
				if err != nil {
					t.Fatal(err)
				}
				defer blocker.Rollback(context.Background())
				if mode != "oldest_locked" {
					if _, err := blocker.Exec(ctx, "SELECT id FROM job_posting WHERE id=$1::uuid FOR UPDATE", id); err != nil {
						t.Fatal(err)
					}
				}
				if mode != "later_locked" {
					if _, err := blocker.Exec(ctx, "SELECT id FROM job_posting WHERE id=$1::uuid FOR UPDATE", f.task.ID); err != nil {
						t.Fatal(err)
					}
				}
				bounded, cancel := context.WithTimeout(ctx, time.Second)
				defer cancel()
				claim, err := a.ClaimDetails(bounded, Simple)
				if err != nil {
					t.Fatal("independent posting selection blocked", err)
				}
				if mode == "both_locked" {
					if claim != nil || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
						t.Fatal("busy canonical postings granted a lease")
					}
					if err := blocker.Rollback(ctx); err != nil {
						t.Fatal(err)
					}
					claim, err = a.ClaimDetails(bounded, Simple)
					if err != nil {
						t.Fatal("released posting did not become claimable", err)
					}
				}
				want := f.task.ID
				if mode == "oldest_locked" {
					want = id
				}
				// Once both rows are released, either first-time posting may
				// satisfy the existing queue's first-time/fairness order.
				if mode == "both_locked" && firstTime && claim != nil && (claim.task.ID == f.task.ID || claim.task.ID == id) {
					want = claim.task.ID
				}
				if claim == nil || claim.task.ID != want || claim.task.Kind != Scrape || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 1 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 1 {
					observed := "nil"
					if claim != nil {
						observed = claim.task.ID
					}
					t.Fatalf("selection lost its exact available posting and token: got=%s want=%s inflight=%d tokens=%d", observed, want, f.client.redis.ZCard(ctx, "inflight:simple").Val(), f.client.redis.HLen(ctx, "inflight_tokens:simple").Val())
				}
			})
		}
	}
}
