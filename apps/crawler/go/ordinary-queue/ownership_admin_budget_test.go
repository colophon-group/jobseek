package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

type ownershipSnapshotDelay struct{ used atomic.Bool }

func (*ownershipSnapshotDelay) DialHook(next redis.DialHook) redis.DialHook          { return next }
func (*ownershipSnapshotDelay) ProcessHook(next redis.ProcessHook) redis.ProcessHook { return next }
func (d *ownershipSnapshotDelay) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, commands []redis.Cmder) error {
		for _, command := range commands {
			if command.Name() == "hgetall" && d.used.CompareAndSwap(false, true) {
				timer := time.NewTimer(16 * time.Second)
				defer timer.Stop()
				select {
				case <-ctx.Done():
					return ctx.Err()
				case <-timer.C:
				}
				break
			}
		}
		return next(ctx, commands)
	}
}

func TestRealOwnershipAdministrativeSnapshotExceedsRuntimeBudgetWithoutSelectingAuthority(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	before := snapshot(t, f.client)
	delay := &ownershipSnapshotDelay{}
	f.client.redis.AddHook(delay)
	plan := stageFixturePlan(t, f, strings.Repeat("a", 40))
	if !delay.used.Load() {
		t.Fatal("snapshot delay not exercised")
	}
	readback, e := f.authority.InspectStagedOwnership(ctx, plan.SHA256(), plan.SourceRevision())
	if e != nil || readback.SHA256() != plan.SHA256() {
		t.Fatal("administrative readback lost staged identity", e)
	}
	var state string
	if e = f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&state); e != nil || state != "staged" {
		t.Fatal("snapshot selected serving authority", e)
	}
	if f.client.redis.Exists(ctx, "ordinary:ownership:active").Val() != 0 {
		t.Fatal("snapshot installed routing authority")
	}
	assertCanonical(t, f, false)
	// Runtime writes still expire before a callback can commit after 16 seconds.
	e = f.authority.transaction(ctx, false, func(call context.Context, tx pgx.Tx) error {
		var idleLimit string
		if err := tx.QueryRow(call, "SHOW idle_in_transaction_session_timeout").Scan(&idleLimit); err != nil || idleLimit != "15s" {
			t.Fatal("administrative idle budget leaked into runtime", err, idleLimit)
		}
		timer := time.NewTimer(16 * time.Second)
		defer timer.Stop()
		select {
		case <-call.Done():
			return call.Err()
		case <-timer.C:
		}
		_, e := tx.Exec(call, "UPDATE job_posting SET titles=ARRAY['Unexpected late runtime effect'] WHERE id=$1::uuid", f.task.ID)
		return e
	})
	if !errors.Is(e, context.DeadlineExceeded) {
		t.Fatal("runtime transaction budget widened", e)
	}
	assertCanonical(t, f, false)
	// The longer administrative ceiling still respects the caller's deadline.
	delay.used.Store(false)
	short, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
	defer cancel()
	if p, e := f.authority.StageOwnership(short, strings.Repeat("b", 40), []string{f.task.ID}, nil); p != nil || e == nil || !errors.Is(short.Err(), context.DeadlineExceeded) {
		t.Fatal("caller cancellation did not abort staging", e)
	}
	var count int
	if e = f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE source_revision=$1 AND routing_epoch=$2", strings.Repeat("b", 40), f.epoch).Scan(&count); e != nil || count != 0 {
		t.Fatal("cancelled staging retained a plan", e)
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("administrative operation changed queue state")
	}
}
