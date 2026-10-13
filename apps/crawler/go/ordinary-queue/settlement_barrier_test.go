package queue

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestRealCompletedSettlementUsesItsReceiptFence(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		t.Run(string(kind), func(t *testing.T) {
			for _, mode := range []string{"unrelated_shared_writer", "same_receipt_writer", "exclusive_reaper"} {
				t.Run(mode, func(t *testing.T) {
					f := realAuthority(t, kind, Simple)
					ctx := context.Background()
					claim := mustClaim(t, f)
					receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
						return effect(ctx, tx, claim, futureDue())
					})
					if err != nil || receipt == nil {
						t.Fatal("completed receipt unavailable", err)
					}
					blocker, err := f.observer.Begin(ctx)
					if err != nil {
						t.Fatal(err)
					}
					defer blocker.Rollback(context.Background())
					switch mode {
					case "unrelated_shared_writer":
						_, err = blocker.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", OrdinaryLeaseBarrier)
					case "exclusive_reaper":
						_, err = blocker.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", OrdinaryLeaseBarrier)
					case "same_receipt_writer":
						_, err = blocker.Exec(ctx, "SELECT task_id FROM ordinary_worker_write_fence WHERE task_kind=$1 AND task_id=$2::uuid FOR UPDATE", string(kind), claim.task.ID)
					}
					if err != nil {
						t.Fatal(err)
					}
					bounded, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
					defer cancel()
					err = f.authority.Settle(bounded, claim, receipt)
					if mode == "unrelated_shared_writer" {
						if err != nil {
							t.Fatal("unrelated shared writer blocked a completed receipt's settlement", err)
						}
						if f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
							t.Fatal("completed receipt did not close its exact lease")
						}
					} else {
						if err == nil || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 1 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 1 {
							t.Fatal("held receipt or lease-ending reaper lost its ordering barrier")
						}
						if err := blocker.Rollback(ctx); err != nil {
							t.Fatal(err)
						}
						if err := f.authority.Settle(ctx, claim, receipt); err != nil {
							t.Fatal("released barrier did not restore settlement", err)
						}
					}
				})
			}
		})
	}
}

func TestRealConcurrentReceiptSettlementClosesOneExactLease(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		t.Run(string(kind), func(t *testing.T) {
			f := realAuthority(t, kind, Simple)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			claim := mustClaim(t, f)
			receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
				return effect(ctx, tx, claim, futureDue())
			})
			if err != nil || receipt == nil {
				t.Fatal("completed receipt unavailable", err)
			}
			start := make(chan struct{})
			results := make(chan error, 2)
			for range 2 {
				go func() { <-start; results <- f.authority.Settle(ctx, claim, receipt) }()
			}
			close(start)
			accepted := 0
			for range 2 {
				err := <-results
				if err == nil {
					accepted++
				} else if !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("concurrent receipt settlement failed unexpectedly", err)
				}
			}
			if accepted != 1 || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
				t.Fatal("concurrent settlement accepted more than one exact lease")
			}
			assertCanonical(t, f, true)
		})
	}
}
