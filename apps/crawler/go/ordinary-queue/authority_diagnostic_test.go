package queue

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestAuthorityLossDiagnosticsPreserveFenceIdentityWithoutMessages(t *testing.T) {
	for _, rejection := range []authorityRejection{epochRejection, cacheSnapshotRejection, leaseRejection, fenceRejection, receiptRejection, acknowledgementRejection} {
		err := authorityError(fmt.Errorf("private configuration and token: %w", rejection))
		if !errors.Is(err, ErrAuthorityLost) || AuthorityLossReason(err) == "" || err.Error() != ErrAuthorityLost.Error() {
			t.Fatal("authority diagnostic lost refusal identity or exposed wrapped message")
		}
	}
	for _, err := range []error{nil, ErrAuthorityLost, errors.New("cache_snapshot"), authorityRejection(255)} {
		if AuthorityLossReason(err) != "" {
			t.Fatal("unknown error supplied authority diagnostic context")
		}
	}
}

func TestRealCacheSnapshotRefusalRetainsCommittedReceiptAndLease(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	claim := mustClaim(t, f)
	ctx := context.Background()
	due := futureDue()
	receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		return effect(ctx, tx, claim, due)
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_slug", "changed-private-value").Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.authority.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) || AuthorityLossReason(err) != "cache_snapshot" {
		t.Fatal("changed snapshot settled a stale receipt", err)
	}
	assertCanonical(t, f, true)
	var state string
	if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_kind='monitor' AND task_id=$1::uuid", f.task.ID).Scan(&state); err != nil || state != "completed" {
		t.Fatal("cache refusal discarded the committed recovery receipt")
	}
	if count, err := f.client.redis.ZCard(ctx, "inflight:"+string(f.task.Worker)).Result(); err != nil || count != 1 {
		t.Fatal("cache refusal acknowledged the retained lease")
	}
}

func TestRealAuthorityLossDistinguishesCacheLeaseAndEpoch(t *testing.T) {
	for _, reason := range []string{"cache_snapshot", "lease", "routing_epoch"} {
		t.Run(reason, func(t *testing.T) {
			f := realAuthority(t, Monitor, Simple)
			claim := mustClaim(t, f)
			ctx := context.Background()
			switch reason {
			case "cache_snapshot":
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_slug", "changed-private-value").Err(); err != nil {
					t.Fatal(err)
				}
			case "lease":
				if err := f.client.redis.Del(ctx, "inflight_tokens:"+string(f.task.Worker)).Err(); err != nil {
					t.Fatal(err)
				}
			case "routing_epoch":
				if _, err := f.observer.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')"); err != nil {
					t.Fatal(err)
				}
			}
			err := f.authority.Heartbeat(ctx, claim)
			if !errors.Is(err, ErrAuthorityLost) || AuthorityLossReason(err) != reason {
				t.Fatalf("authority refusal lost bounded reason: %v", err)
			}
			assertCanonical(t, f, false)
		})
	}
}
