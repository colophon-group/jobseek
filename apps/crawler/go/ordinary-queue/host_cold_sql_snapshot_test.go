package queue

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"strings"
	"testing"
	"time"
)

func TestColdCanonicalSnapshotOutlivesRuntimeCapWithoutReleasingBarriers(t *testing.T) {
	pool := hostSQLFixture(t)
	table := "cold_snapshot_" + strings.ReplaceAll(ordinaryID(t), "-", "")
	if _, err := pool.Exec(context.Background(), "CREATE TABLE "+table+"(phase text PRIMARY KEY)"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = pool.Exec(context.Background(), "DROP TABLE "+table) })
	for _, administrative := range []bool{false, true} {
		err := WithHostColdSQL(context.Background(), pool, hostSQLBindingFixture(), func(ctx context.Context, scope *HostColdSQL) error {
			transaction := coldTransitionTransaction
			if administrative {
				transaction = coldCanonicalSnapshotTransaction
			}
			err := transaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
				if _, err := tx.Exec(ctx, "INSERT INTO "+table+" VALUES('snapshot')"); err != nil {
					return err
				}
				// Models client-side full-cohort parsing after its last SQL statement.
				// The old 15-second context and server idle ceiling both reject this.
				timer := time.NewTimer(16 * time.Second)
				defer timer.Stop()
				select {
				case <-ctx.Done():
					return ctx.Err()
				case <-timer.C:
				}
				for _, key := range hostSQLBarriers {
					var entered bool
					if err := pool.QueryRow(ctx, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered); err != nil {
						return err
					}
					if entered {
						t.Fatal("writer entered held administrative snapshot")
					}
				}
				var count int
				if err := tx.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count); err != nil {
					return err
				}
				if count != 1 {
					t.Fatal("snapshot transaction lost its rows")
				}
				return nil
			})
			if err == nil {
				return scope.Check(ctx)
			}
			return err
		})
		if administrative {
			if err != nil {
				t.Fatal("bounded full-cohort snapshot failed", err)
			}
		} else if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("runtime-sized snapshot did not reproduce deadline", err)
		}
		hostSQLAssertReleased(t, pool)
	}
	var count int
	if err := pool.QueryRow(context.Background(), "SELECT count(*) FROM "+table).Scan(&count); err != nil || count != 1 {
		t.Fatal("failed snapshot committed or successful snapshot lost", err, count)
	}
}

func TestColdCanonicalSnapshotRequiresLiveScopeAndHonorsCallerCancellation(t *testing.T) {
	pool := hostSQLFixture(t)
	if err := coldCanonicalSnapshotTransaction(context.Background(), pool, func(context.Context, pgx.Tx) error { t.Fatal("unscoped snapshot contacted SQL"); return nil }); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unscoped snapshot admitted", err)
	}
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	err := WithHostColdSQL(parent, pool, hostSQLBindingFixture(), func(ctx context.Context, scope *HostColdSQL) error {
		cancel()
		return coldCanonicalSnapshotTransaction(ctx, pool, func(context.Context, pgx.Tx) error { t.Fatal("cancelled snapshot ran"); return nil })
	})
	if err == nil {
		t.Fatal("administrative snapshot extended caller lifetime")
	}
	hostSQLAssertReleased(t, pool)
}
