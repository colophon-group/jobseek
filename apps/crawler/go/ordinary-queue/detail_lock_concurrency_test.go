package queue

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// Different detail postings share a canonical board. Their observation locks
// must remain compatible through posting/fence validation, instead of both
// upgrading a shared board lock and deadlocking before any fetch.
func TestRealConcurrentDetailValidationDoesNotUpgradeSharedBoardLock(t *testing.T) {
	f, first := currentWorkdayDetailFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	id := ordinaryID(t)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", id)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
	})
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at)
 SELECT $2::uuid,company_id,board_id,source_url||$2::text,titles,locales,next_scrape_at FROM job_posting WHERE id=$1::uuid`, first.task.ID, id); err != nil {
		t.Fatal(err)
	}
	// The second retained fixture attempt has its own posting and fence. This
	// test exercises SQL validation only; it never grants a queue lease or work.
	second := &Claim{owner: first.owner, task: first.task, boardID: first.boardID, configDigest: first.configDigest}
	second.task.ID = id
	if _, err := f.observer.Exec(ctx, `INSERT INTO ordinary_worker_write_fence(task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state)
 SELECT task_kind,$2::uuid,board_id,routing_epoch,claim_token,config_sha256,state FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid`, first.task.ID, id); err != nil {
		t.Fatal(err)
	}
	ready := make(chan struct{}, 2)
	release := make(chan struct{})
	done := make(chan error, 2)
	for _, claim := range []*Claim{first, second} {
		go func(claim *Claim) {
			done <- f.authority.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
				if _, _, err := f.authority.observeBoardConfigsState(ctx, tx, claim.boardID, false); err != nil {
					return err
				}
				ready <- struct{}{}
				select {
				case <-release:
				case <-ctx.Done():
					return ctx.Err()
				}
				_, err := f.authority.require(ctx, tx, claim, "active")
				return err
			})
		}(claim)
	}
	for range 2 {
		select {
		case <-ready:
		case <-ctx.Done():
			t.Fatal("independent board observations blocked")
		}
	}
	close(release)
	for range 2 {
		select {
		case err := <-done:
			if err != nil {
				t.Fatalf("independent detail validation rejected: %v", err)
			}
		case <-ctx.Done():
			t.Fatal("detail validation blocked on board lock upgrade")
		}
	}
}
