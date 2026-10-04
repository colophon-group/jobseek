package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func reapCommittedFixture(t *testing.T, p firstOwnerFixture, before ...func(context.Context) error) ([]any, error) {
	t.Helper()
	// Production's reaper has one connection. The observation must use the
	// barrier's transaction rather than opening another pool connection.
	config, err := pgxpool.ParseConfig(p.f.dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.MinConns, config.MaxConns = 0, 1
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var result []any
	err = WithOrdinaryLeaseReaping(ctx, pool, p.f.client.redis, "simple", 10, func(ctx context.Context, now float64, observed, projection string) error {
		for _, inject := range before {
			if err := inject(ctx); err != nil {
				return err
			}
		}
		var err error
		result, err = p.f.client.redis.Eval(ctx, reaperSource(t), nil, "simple", number(now), 10, 3, number(now), "guarded", observed, projection).Slice()
		return err
	})
	return result, err
}

func TestRealCommittedLeaseReapingRechecksAtomicObservation(t *testing.T) {
	for _, mode := range []string{"batch", "token", "config", "projection"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			if _, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, futureDue())
				return err
			}); err != nil {
				t.Fatal(err)
			}
			task := inflight(p.f.task)
			if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: task}).Err(); err != nil {
				t.Fatal(err)
			}
			var before map[string]string
			// Inject after the SQL observation, immediately before Redis CAS.
			_, err := reapCommittedFixture(t, p, func(ctx context.Context) error {
				var err error
				switch mode {
				case "batch":
					err = p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 0, Member: "monitor|foreign.invalid|foreign"}).Err()
				case "token":
					err = p.f.client.redis.HSet(ctx, "inflight_tokens:simple", task, strings.Repeat("f", 32)).Err()
				case "config":
					err = p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "egress_host", "changed.greenhouse.io").Err()
				case "projection":
					err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "{}", 0).Err()
				}
				before = snapshot(t, p.f.client)
				return err
			})
			if err == nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("changed atomic observation had effects", err)
			}
		})
	}
}

func TestRealCommittedLeaseReapingPreservesSuccessfulDeadline(t *testing.T) {
	for _, mode := range []string{"future", "already-due", "stale-queued", "repair", "metadata-change", "learned-host", "last-strike"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			due := futureDue()
			if mode == "already-due" {
				due = time.Now().UTC().Add(-time.Minute).Truncate(time.Microsecond)
			}
			host := "boards-api.greenhouse.io"
			learned := &host
			if _, err := a.write(ctx, claim, true, &learned, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
				return err
			}); err != nil {
				t.Fatal(err)
			}
			// Omit settlement ACK, then let the real lease expire. Reaping is
			// recovery of committed success, not another failed network attempt.
			task := inflight(p.f.task)
			if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: task}).Err(); err != nil {
				t.Fatal(err)
			}
			want := due
			switch mode {
			case "stale-queued":
				if err := p.f.client.redis.ZAdd(ctx, "monitors_simple:"+p.f.task.Domain, redis.Z{Score: 2, Member: p.f.task.ID}).Err(); err != nil {
					t.Fatal(err)
				}
			case "repair":
				want = due.Add(-30 * time.Minute)
				if err := p.f.client.redis.HSet(ctx, "monitor_repair_due:simple", task, number(seconds(want))).Err(); err != nil {
					t.Fatal(err)
				}
			case "metadata-change":
				if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "egress_host", "changed.greenhouse.io").Err(); err != nil {
					t.Fatal(err)
				}
			case "last-strike":
				if err := p.f.client.redis.HSet(ctx, "inflight_strikes:simple", task, "2").Err(); err != nil {
					t.Fatal(err)
				}
			}
			canonical := coldCanonicalSnapshot(t, p.f)
			result, err := reapCommittedFixture(t, p)
			if err != nil || !reflect.DeepEqual(result, []any{int64(1), int64(0), int64(0)}) {
				t.Fatal("committed reaping failed", result, err)
			}
			score, err := p.f.client.redis.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Result()
			if err != nil || score != seconds(want) {
				t.Fatal("committed deadline became a retry", score, seconds(want), err)
			}
			if canonical != coldCanonicalSnapshot(t, p.f) || p.f.client.redis.HExists(ctx, "inflight_tokens:simple", task).Val() || p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || p.f.client.redis.HExists(ctx, "inflight_strikes:simple", task).Val() || p.f.client.redis.ZCard(ctx, "deadletter:simple").Val() != 0 {
				t.Fatal("receipt or successful recovery changed")
			}
			if mode == "learned-host" && p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "egress_host").Val() != host {
				t.Fatal("committed learned host lost")
			}
			if mode == "metadata-change" && p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "egress_host").Val() != "changed.greenhouse.io" {
				t.Fatal("stale learned host replayed")
			}
			before := snapshot(t, p.f.client)
			if _, err := reapCommittedFixture(t, p); err != nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("repeated reaper changed settled queue", err)
			}
		})
	}
}

func TestRealCommittedLeaseReapingRefusesChangedAuthorityBeforeEffects(t *testing.T) {
	for _, mode := range []string{"projection", "canonical-deadline", "config", "ready-index"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			if _, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, futureDue())
				return err
			}); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: inflight(p.f.task)}).Err(); err != nil {
				t.Fatal(err)
			}
			var err error
			switch mode {
			case "projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "{}", 0).Err()
			case "canonical-deadline":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=next_check_at+interval '1 minute' WHERE id=$1::uuid", p.f.task.ID)
			case "config":
				err = p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "120").Err()
			case "ready-index":
				err = p.f.client.redis.Set(ctx, "ready:simple:1", "corrupt", 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			if _, err := reapCommittedFixture(t, p); err == nil {
				t.Fatal("changed receipt authority admitted")
			}
			if canonical != coldCanonicalSnapshot(t, p.f) || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("refused sweep had effects")
			}
		})
	}
}

func TestRealCommittedLeaseReapingKeepsUnfinishedRetry(t *testing.T) {
	for _, mode := range []string{"unfinished", "foreign-token"} {
		t.Run(mode, func(t *testing.T) {
			foreignToken := mode == "foreign-token"
			p := firstOwnershipFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			if foreignToken {
				if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { return nil }); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.HSet(ctx, "inflight_tokens:simple", inflight(p.f.task), strings.Repeat("f", 32)).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: inflight(p.f.task)}).Err(); err != nil {
				t.Fatal(err)
			}
			canonical := coldCanonicalSnapshot(t, p.f)
			before := time.Now().Add(-time.Second)
			if _, err := reapCommittedFixture(t, p); err != nil {
				t.Fatal(err)
			}
			score := p.f.client.redis.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Val()
			if score < seconds(before) || score > seconds(time.Now().Add(time.Second)) || p.f.client.redis.HGet(ctx, "inflight_strikes:simple", inflight(p.f.task)).Val() != "1" || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("unfinished attempt inherited successful receipt")
			}
			if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("reaped attempt retained authority", err)
			}
		})
	}
}
