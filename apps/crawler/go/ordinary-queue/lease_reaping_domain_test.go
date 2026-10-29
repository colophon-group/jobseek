package queue

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func TestRealDefaultCommittedReapingRetiresOldDomain(t *testing.T) {
	for _, sourceWorker := range []WorkerType{Simple, Browser} {
		for _, mode := range []string{"missing", "queued", "first", "inflight", "deadletter", "target-token", "target-ready", "target-rate", "changed-config", "changed-deadline", "changed-token", "producer-owner"} {
			t.Run(string(sourceWorker)+"/"+mode, func(t *testing.T) {
				p := firstOwnerFixture{f: greenhouseAuthorityFixture(t)}
				ctx, r := context.Background(), p.f.client.redis
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
				if _, err = a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
					_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
					return err
				}); err != nil {
					t.Fatal(err)
				}
				targetMember := inflight(p.f.task)
				oldDomain := "retired.greenhouse.invalid"
				sourceMember := "monitor|" + oldDomain + "|" + p.f.task.ID
				token := r.HGet(ctx, "inflight_tokens:simple", targetMember).Val()
				pipe := r.TxPipeline()
				pipe.ZRem(ctx, "inflight:simple", targetMember)
				pipe.HDel(ctx, "inflight_tokens:simple", targetMember)
				pipe.ZAdd(ctx, "inflight:"+string(sourceWorker), redis.Z{Member: sourceMember, Score: 1})
				pipe.HSet(ctx, "inflight_tokens:"+string(sourceWorker), sourceMember, token)
				pipe.HSet(ctx, "inflight_strikes:"+string(sourceWorker), sourceMember, 4)
				pipe.ZAdd(ctx, "monitors_"+string(sourceWorker)+":"+oldDomain, redis.Z{Member: p.f.task.ID, Score: 2})
				pipe.ZAdd(ctx, "ready:"+string(sourceWorker)+":1", redis.Z{Member: oldDomain, Score: 2})
				if _, err = pipe.Exec(ctx); err != nil {
					t.Fatal(err)
				}
				targetDue := seconds(due) + 100
				switch mode {
				case "queued":
					err = r.ZAdd(ctx, "monitors_simple:"+p.f.task.Domain, redis.Z{Member: p.f.task.ID, Score: targetDue}).Err()
				case "first":
					err = r.ZAdd(ctx, "ft_monitors_simple:"+p.f.task.Domain, redis.Z{Member: p.f.task.ID, Score: targetDue}).Err()
				case "inflight":
					err = r.ZAdd(ctx, "inflight:simple", redis.Z{Member: targetMember, Score: targetDue}).Err()
					if err == nil {
						err = r.HSet(ctx, "inflight_tokens:simple", targetMember, strings.Repeat("a", 32)).Err()
					}
				case "deadletter":
					err = r.ZAdd(ctx, "deadletter:simple", redis.Z{Member: targetMember, Score: targetDue}).Err()
				case "target-token":
					err = r.HSet(ctx, "inflight_tokens:simple", targetMember, strings.Repeat("a", 32)).Err()
				case "target-ready":
					err = r.Set(ctx, "ready:simple:1", "corrupt", 0).Err()
				case "target-rate":
					err = r.Set(ctx, "ratelimit:"+p.f.task.Domain, "NaN", 0).Err()
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
				p.f.task.Worker = sourceWorker
				before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
				cacheBefore := r.HGetAll(ctx, "board:"+p.f.task.ID).Val()
				var result []any
				if mode == "changed-token" {
					result, err = reapCommittedFixture(t, p, func(ctx context.Context) error {
						err := r.HSet(ctx, "inflight_tokens:"+string(sourceWorker), sourceMember, strings.Repeat("f", 32)).Err()
						before = snapshot(t, p.f.client)
						return err
					})
				} else {
					result, err = reapCommittedFixture(t, p)
				}
				refuse := strings.HasPrefix(mode, "target-") || strings.HasPrefix(mode, "changed-") || mode == "producer-owner"
				if refuse {
					if err == nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
						t.Fatal("refused domain recovery changed state", err)
					}
					return
				}
				if err != nil || !reflect.DeepEqual(result, []any{int64(1), int64(0), int64(0)}) {
					t.Fatal("domain recovery failed", result, err)
				}
				if canonical != coldCanonicalSnapshot(t, p.f) || !reflect.DeepEqual(cacheBefore, r.HGetAll(ctx, "board:"+p.f.task.ID).Val()) || r.HExists(ctx, "inflight_tokens:"+string(sourceWorker), sourceMember).Val() || r.ZScore(ctx, "inflight:"+string(sourceWorker), sourceMember).Err() != redis.Nil || r.ZScore(ctx, "monitors_"+string(sourceWorker)+":"+oldDomain, p.f.task.ID).Err() != redis.Nil || r.ZScore(ctx, "ready:"+string(sourceWorker)+":1", oldDomain).Err() != redis.Nil || r.HExists(ctx, "inflight_strikes:"+string(sourceWorker), sourceMember).Val() {
					t.Fatal("source retirement changed canonical/cache state or retained stale ownership")
				}
				switch mode {
				case "queued", "first":
					prefix := "monitors_simple:"
					if mode == "first" {
						prefix = "ft_monitors_simple:"
					}
					if r.ZScore(ctx, prefix+p.f.task.Domain, p.f.task.ID).Val() != targetDue {
						t.Fatal("newer target deadline changed")
					}
				case "inflight":
					if r.HGet(ctx, "inflight_tokens:simple", targetMember).Val() != strings.Repeat("a", 32) || r.ZScore(ctx, "inflight:simple", targetMember).Val() != targetDue {
						t.Fatal("newer target attempt changed")
					}
				case "deadletter":
					if r.ZScore(ctx, "deadletter:simple", targetMember).Val() != targetDue {
						t.Fatal("target disposition changed")
					}
				default:
					if r.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Val() != seconds(due) {
						t.Fatal("canonical target deadline lost")
					}
				}
				if mode == "first" || mode == "inflight" || mode == "deadletter" {
					if r.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Err() != redis.Nil {
						t.Fatal("target disposition also received a recurring schedule")
					}
				}
				before = snapshot(t, p.f.client)
				if _, err = reapCommittedFixture(t, p); err != nil || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
					t.Fatal("repeated domain recovery changed state", err)
				}
			})
		}
	}
}
