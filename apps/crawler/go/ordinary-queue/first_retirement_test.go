package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func firstRetirementClaim(t *testing.T, p firstOwnerFixture) (*Authority, *Claim) {
	t.Helper()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(context.Background(), p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	claim, err := a.Claim(context.Background(), Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != p.f.task.ID {
		t.Fatal("owned retirement claim unavailable", err)
	}
	return a, claim
}

func firstRetirementDue(t *testing.T, p firstOwnerFixture) *time.Time {
	t.Helper()
	var due *time.Time
	if err := p.f.observer.QueryRow(context.Background(), "SELECT CASE WHEN is_enabled THEN next_check_at END FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&due); err != nil {
		t.Fatal(err)
	}
	return due
}

func assertFirstRetirementSchedule(t *testing.T, p firstOwnerFixture, due *time.Time) {
	t.Helper()
	ctx := context.Background()
	task := inflight(p.f.task)
	if p.f.client.redis.ZScore(ctx, "inflight:simple", task).Err() != redis.Nil || p.f.client.redis.HExists(ctx, "inflight_tokens:simple", task).Val() || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("retirement retained a native lease or ownership projection")
	}
	key := "monitors_simple:" + p.f.task.Domain
	score, err := p.f.client.redis.ZScore(ctx, key, p.f.task.ID).Result()
	if due == nil {
		if err != redis.Nil {
			t.Fatal("disabled monitor was rescheduled", err)
		}
	} else if err != nil || score != seconds(*due) {
		t.Fatal("canonical retirement deadline lost", err, score, seconds(*due))
	}
}

func TestRealFirstRetirementInterruptedClaimKeepsCanonicalReceiptsAndRevokesWriter(t *testing.T) {
	for _, mode := range []string{"active", "claim-before-sql", "stale-sql-token", "disabled"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			var err error
			switch mode {
			case "claim-before-sql":
				// Represent the Redis-claim / SQL-activation seam. This private
				// fixture owns its rows; retirement must not fabricate a receipt.
				_, err = p.f.observer.Exec(ctx, "DELETE FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", p.f.task.ID)
			case "stale-sql-token":
				_, err = p.f.observer.Exec(ctx, "UPDATE ordinary_worker_write_fence SET claim_token=$2 WHERE task_id=$1::uuid", p.f.task.ID, strings.Repeat("f", 32))
			case "disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.f.task.ID)
			}
			if err != nil {
				t.Fatal(err)
			}
			canonical, due := coldCanonicalSnapshot(t, p.f), firstRetirementDue(t, p)
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("cold retirement required the interrupted worker", err)
			}
			assertFirstRetirementSchedule(t, p, due)
			if canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("retirement rewrote canonical data or retained attempts")
			}
			if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retired attempt retained heartbeat authority", err)
			}
			called := false
			if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
				t.Fatal("retired attempt retained database authority", err)
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			assertFirstRetirementSchedule(t, p, due)
			firstFixtureSave(t, p, false)
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("completed retirement repeated SAVE", err)
			}
			if due != nil {
				if err := p.f.client.redis.Del(ctx, "ratelimit:"+p.f.task.Domain).Err(); err != nil {
					t.Fatal(err)
				}
				if task, err := p.f.client.Claim(ctx, Simple); err != nil || task == nil || task.ID != p.f.task.ID {
					t.Fatal("restored legacy monitor unavailable", err)
				}
			}
		})
	}
}

func TestRealFirstRetirementCommittedBeforeACKRestoresFutureAndLearnedHost(t *testing.T) {
	for _, mode := range []string{"leased", "reaped", "save-failure"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			due := time.Now().UTC().Add(time.Hour).Truncate(time.Microsecond)
			host := "boards-api.greenhouse.io"
			learned := &host
			if _, err := a.write(ctx, claim, true, &learned, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
				return err
			}); err != nil {
				t.Fatal("private native commit", err)
			}
			if mode == "reaped" {
				if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: inflight(p.f.task)}).Err(); err != nil {
					t.Fatal(err)
				}
				if _, err := guardedReap(t, p.f); err != nil {
					t.Fatal("real reaper failed", err)
				}
			}
			canonical := coldCanonicalSnapshot(t, p.f)
			if mode == "save-failure" {
				firstFixtureSave(t, p, false)
				if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrObservation) || firstFixtureState(t, p) != "active" {
					t.Fatal("unacknowledged interrupted retirement retired SQL", err)
				}
				assertFirstRetirementSchedule(t, p, &due)
				firstFixtureSave(t, p, true)
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("committed interrupted monitor could not retire", err)
			}
			assertFirstRetirementSchedule(t, p, &due)
			if canonical != coldCanonicalSnapshot(t, p.f) || p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "egress_host").Val() != host || p.f.client.redis.HExists(ctx, "inflight_strikes:simple", inflight(p.f.task)).Val() {
				t.Fatal("committed receipt, learned host or settlement state lost")
			}
		})
	}
}

func TestRealFirstRetirementPreflightsBeforeAnyEffect(t *testing.T) {
	for _, mode := range []string{"wrong-type", "tokenless", "orphan-token", "foreign-projection", "b0-lease", "changed-config", "foreign-sql-attempt", "invalid-queue-tail"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			_, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			var err error
			switch mode {
			case "wrong-type":
				err = p.f.client.redis.Set(ctx, "ft_scrapes_simple:"+p.f.task.Domain, "corrupt", 0).Err()
			case "tokenless":
				err = p.f.client.redis.HDel(ctx, "inflight_tokens:simple", inflight(p.f.task)).Err()
			case "orphan-token":
				err = p.f.client.redis.ZRem(ctx, "inflight:simple", inflight(p.f.task)).Err()
			case "foreign-projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "foreign", 0).Err()
			case "b0-lease":
				err = p.f.client.redis.ZAdd(ctx, p.target.keys()[3], redis.Z{Score: 1, Member: "untracked"}).Err()
			case "changed-config":
				err = p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Err()
			case "foreign-sql-attempt":
				_, err = p.f.observer.Exec(ctx, "UPDATE ordinary_worker_write_fence SET task_kind='scrape' WHERE task_id=$1::uuid", p.f.task.ID)
			case "invalid-queue-tail":
				err = p.f.client.redis.Do(ctx, "ZADD", "monitors_simple:"+claim.Descriptor().Domain, "+inf", "foreign").Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unsafe retirement admitted", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "active" {
				t.Fatal("rejected retirement partially changed state")
			}
		})
	}
}

func TestRealFirstRetirementPreservesSharedDomainPriorityRepairAndB0(t *testing.T) {
	p := firstOwnershipFixture(t)
	_, _ = firstRetirementClaim(t, p)
	ctx := context.Background()
	due := firstRetirementDue(t, p)
	if due == nil {
		t.Fatal("fixture canonical deadline missing")
	}
	repair := due.Add(-time.Minute)
	for key, member := range map[string]string{"ft_scrapes_simple:greenhouse": "foreign-first", "scrapes_simple:greenhouse": "foreign-recurring", "ready:rotation:simple": "greenhouse"} {
		if err := p.f.client.redis.ZAdd(ctx, key, redis.Z{Score: 123, Member: member}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	if err := p.f.client.redis.Set(ctx, "ratelimit:greenhouse", 500, 0).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "monitor_repair_due:simple", inflight(p.f.task), number(seconds(repair))).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "inflight_strikes:simple", inflight(p.f.task), 2).Err(); err != nil {
		t.Fatal(err)
	}
	before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("shared domain retirement", err)
	}
	assertFirstRetirementSchedule(t, p, &repair)
	if p.f.client.redis.ZScore(ctx, "ready:simple:0", "greenhouse").Val() != 500 || p.f.client.redis.ZScore(ctx, "ready:simple:1", "greenhouse").Err() != redis.Nil || p.f.client.redis.ZScore(ctx, "ready:simple:2", "greenhouse").Err() != redis.Nil {
		t.Fatal("retirement lost strict first-time domain priority")
	}
	if p.f.client.redis.HGet(ctx, "inflight_strikes:simple", inflight(p.f.task)).Val() != "2" || p.f.client.redis.HExists(ctx, "monitor_repair_due:simple", inflight(p.f.task)).Val() {
		t.Fatal("retirement invented success or lost a deferred repair")
	}
	after := snapshot(t, p.f.client)
	for key, value := range before {
		if strings.HasPrefix(key, "lightpanda-b0:") || key == "ft_scrapes_simple:greenhouse" || key == "scrapes_simple:greenhouse" || key == "ready:rotation:simple" || key == "ratelimit:greenhouse" {
			if !reflect.DeepEqual(value, after[key]) {
				t.Fatal("retirement changed foreign or B0 state", key)
			}
		}
	}
	if canonical != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("shared domain retirement changed canonical receipts")
	}
}
