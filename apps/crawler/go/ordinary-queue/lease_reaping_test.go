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
	err = WithOrdinaryLeaseReaping(ctx, pool, p.f.client.redis, string(p.f.task.Worker), 10, func(ctx context.Context, now float64, observed, projection string) error {
		for _, inject := range before {
			if err := inject(ctx); err != nil {
				return err
			}
		}
		var err error
		result, err = p.f.client.redis.Eval(ctx, reaperSource(t), nil, string(p.f.task.Worker), number(now), 10, 3, number(now), "guarded", observed, projection).Slice()
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
	for _, mode := range []string{"future", "already-due", "stale-queued", "repair", "metadata-change", "learned-host", "last-strike", "default", "default-metadata-change"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			var p firstOwnerFixture
			var a *Authority
			var claim *Claim
			if strings.HasPrefix(mode, "default") {
				p.f = greenhouseAuthorityFixture(t)
				var err error
				a, err = OpenDefaultAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(a.Close)
				claim, err = a.Claim(ctx, Simple)
				if err != nil || claim == nil {
					t.Fatal("default claim unavailable", err)
				}
			} else {
				p = firstOwnershipFixture(t)
				a, claim = firstRetirementClaim(t, p)
			}
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
			case "metadata-change", "default-metadata-change":
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
			if strings.HasSuffix(mode, "metadata-change") && p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "egress_host").Val() != "changed.greenhouse.io" {
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

func TestDefaultCommittedReapingRejectsChangedCrawlerConfiguration(t *testing.T) {
	p := firstOwnerFixture{f: greenhouseAuthorityFixture(t)}
	ctx := context.Background()
	a, err := OpenDefaultAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal("default claim unavailable", err)
	}
	if _, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, futureDue())
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET check_interval_minutes=61 WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "61").Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Member: inflight(p.f.task), Score: 1}).Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, p.f.client)
	if _, err := reapCommittedFixture(t, p); err == nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("changed stable configuration granted receipt recovery", err)
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

// A stale browser attempt can outlive a canonical HTTP route. A committed
// receipt must retire that exact source generation without replaying its work
// or changing a newer target attempt/deadline.
func TestRealDefaultCommittedReapingRetiresWrongLane(t *testing.T) {
	for _, mode := range []string{"missing", "queued", "first", "inflight", "deadletter", "target-corrupt", "target-token", "target-rate", "target-rotation", "changed-token", "changed-config", "changed-deadline", "producer-owner"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnerFixture{f: greenhouseAuthorityFixture(t)}
			ctx := context.Background()
			r := p.f.client.redis
			a, err := OpenDefaultAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(a.Close)
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("default claim unavailable", err)
			}
			due := futureDue()
			learned := "learned.greenhouse.io"
			learnedPtr := &learned
			if _, err := a.write(ctx, claim, true, &learnedPtr, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
				return err
			}); err != nil {
				t.Fatal(err)
			}
			member := inflight(p.f.task)
			token := r.HGet(ctx, "inflight_tokens:simple", member).Val()
			pipe := r.TxPipeline()
			pipe.ZRem(ctx, "inflight:simple", member)
			pipe.HDel(ctx, "inflight_tokens:simple", member)
			pipe.ZAdd(ctx, "inflight:browser", redis.Z{Member: member, Score: 1})
			pipe.HSet(ctx, "inflight_tokens:browser", member, token)
			pipe.ZAdd(ctx, "monitors_browser:"+p.f.task.Domain, redis.Z{Member: p.f.task.ID, Score: 2})
			pipe.ZAdd(ctx, "ready:browser:1", redis.Z{Member: p.f.task.Domain, Score: 2})
			if _, err := pipe.Exec(ctx); err != nil {
				t.Fatal(err)
			}
			targetDue := seconds(due) + 100
			switch mode {
			case "queued":
				err = r.ZAdd(ctx, "monitors_simple:"+p.f.task.Domain, redis.Z{Member: p.f.task.ID, Score: targetDue}).Err()
			case "first":
				err = r.ZAdd(ctx, "ft_monitors_simple:"+p.f.task.Domain, redis.Z{Member: p.f.task.ID, Score: targetDue}).Err()
			case "inflight":
				if err = r.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: targetDue}).Err(); err == nil {
					err = r.HSet(ctx, "inflight_tokens:simple", member, strings.Repeat("a", 32)).Err()
				}
			case "deadletter":
				err = r.ZAdd(ctx, "deadletter:simple", redis.Z{Member: member, Score: targetDue}).Err()
			case "target-corrupt":
				err = r.Set(ctx, "ready:simple:1", "corrupt", 0).Err()
			case "target-token":
				err = r.HSet(ctx, "inflight_tokens:simple", member, strings.Repeat("a", 32)).Err()
			case "target-rate":
				err = r.Set(ctx, "ratelimit:"+p.f.task.Domain, "NaN", 0).Err()
			case "target-rotation":
				err = r.ZAdd(ctx, "ready:rotation:simple", redis.Z{Member: p.f.task.Domain, Score: 1e300}).Err()
			case "changed-token": // Lua CAS must reject a post-observation replacement.
			case "changed-config":
				err = r.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "120").Err()
			case "changed-deadline":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=next_check_at+interval '1 minute' WHERE id=$1::uuid", p.f.task.ID)
			case "producer-owner":
				err = r.Set(ctx, "lightpanda-b0:producer-owner", "{}", 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			cacheBefore := r.HGetAll(ctx, "board:"+p.f.task.ID).Val()
			p.f.task.Worker = Browser
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			var result []any
			if mode == "changed-token" {
				result, err = reapCommittedFixture(t, p, func(ctx context.Context) error {
					err := r.HSet(ctx, "inflight_tokens:browser", member, strings.Repeat("f", 32)).Err()
					before = snapshot(t, p.f.client)
					return err
				})
			} else {
				result, err = reapCommittedFixture(t, p)
			}
			refuse := strings.HasPrefix(mode, "target-") && mode != "target-rotation" || strings.HasPrefix(mode, "changed-") || mode == "producer-owner"
			if refuse {
				if err == nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
					t.Fatal("refused reroute had effects", err)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(result, []any{int64(1), int64(0), int64(0)}) {
				t.Fatal("reroute failed", result, err)
			}
			if !reflect.DeepEqual(cacheBefore, r.HGetAll(ctx, "board:"+p.f.task.ID).Val()) {
				t.Fatal("reroute changed target runtime cache")
			}
			if canonical != coldCanonicalSnapshot(t, p.f) || r.HExists(ctx, "inflight_tokens:browser", member).Val() || r.ZCard(ctx, "inflight:browser").Val() != 0 || r.ZCard(ctx, "monitors_browser:"+p.f.task.Domain).Val() != 0 || r.ZScore(ctx, "ready:browser:1", p.f.task.Domain).Err() != redis.Nil {
				t.Fatal("source retirement or canonical state changed")
			}
			switch mode {
			case "queued":
				if r.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Val() != targetDue {
					t.Fatal("target deadline changed")
				}
			case "first":
				if r.ZScore(ctx, "ft_monitors_simple:"+p.f.task.Domain, p.f.task.ID).Val() != targetDue || r.ZCard(ctx, "monitors_simple:"+p.f.task.Domain).Val() != 0 {
					t.Fatal("target first schedule changed")
				}
			case "inflight":
				if r.HGet(ctx, "inflight_tokens:simple", member).Val() != strings.Repeat("a", 32) || r.ZScore(ctx, "inflight:simple", member).Val() != targetDue || r.ZCard(ctx, "monitors_simple:"+p.f.task.Domain).Val() != 0 {
					t.Fatal("target claim changed")
				}
			case "deadletter":
				if r.ZScore(ctx, "deadletter:simple", member).Val() != targetDue || r.ZCard(ctx, "monitors_simple:"+p.f.task.Domain).Val() != 0 {
					t.Fatal("target disposition changed")
				}
			default:
				if r.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Val() != seconds(due) {
					t.Fatal("canonical deadline lost")
				}
			}
			before = snapshot(t, p.f.client)
			if _, err := reapCommittedFixture(t, p); err != nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("repeat changed settled state", err)
			}
		})
	}
}
