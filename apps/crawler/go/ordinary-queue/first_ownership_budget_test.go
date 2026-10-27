package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"
)

func TestFirstOwnershipEligibilityUsesAdministrativeSnapshotBudget(t *testing.T) {
	p := firstOwnershipFixture(t)
	before := snapshot(t, p.f.client)
	delay := &ownershipSnapshotDelay{}
	p.f.client.redis.AddHook(delay)

	parent, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	err := WithHostColdSQLLifecycle(parent, p.f.observer, hostSQLBindingFixture(), func(ctx context.Context, scope *HostColdSQL) error {
		plan, state, err := firstOwnershipPlan(ctx, p.f.observer, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision(), p.target, false)
		if err != nil {
			return err
		}
		if plan.SHA256() != p.plan.SHA256() || state != "staged" {
			t.Fatal("eligibility changed the staged decision")
		}
		return scope.Check(ctx)
	})
	if err != nil || !delay.used.Load() {
		t.Fatal("full eligibility snapshot expired at the runtime transaction budget", err)
	}
	if firstFixtureState(t, p) != "staged" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("eligibility changed SQL ownership or queue state")
	}
	hostSQLAssertReleased(t, p.f.observer)

	delay.used.Store(false)
	short, stop := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer stop()
	err = WithHostColdSQLLifecycle(short, p.f.observer, hostSQLBindingFixture(), func(ctx context.Context, _ *HostColdSQL) error {
		_, _, err := firstOwnershipPlan(ctx, p.f.observer, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision(), p.target, false)
		return err
	})
	if err == nil || !errors.Is(short.Err(), context.DeadlineExceeded) {
		t.Fatal("eligibility extended its caller deadline", err)
	}
	if firstFixtureState(t, p) != "staged" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("cancelled eligibility changed the retained decision")
	}
	hostSQLAssertReleased(t, p.f.observer)
}
