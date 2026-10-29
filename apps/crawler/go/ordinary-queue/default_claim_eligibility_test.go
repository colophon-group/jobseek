package queue

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func TestRealDefaultIneligibleClaimsKeepOriginalRecovery(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		for _, worker := range []WorkerType{Simple, Browser} {
			for _, mode := range []string{"disabled", "disabled-scheduled", "blocked"} {
				for _, oldFence := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s/%s/old-fence-%t", kind, worker, mode, oldFence), func(t *testing.T) {
						var f authorityFixture
						if kind == Scrape {
							f = currentWorkdayDetailQueueFixture(t)
							f.task.Domain = "fixture.wd1.myworkdayjobs.com"
						} else {
							f = greenhouseAuthorityFixture(t)
						}
						ctx := context.Background()
						a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
						if err != nil {
							t.Fatal(err)
						}
						t.Cleanup(a.Close)
						if worker == Browser {
							prefix, tier := "monitors_", ":1"
							if kind == Scrape {
								prefix, tier = "scrapes_", ":2"
							}
							score, err := f.client.redis.ZScore(ctx, "ready:simple"+tier, f.task.Domain).Result()
							if err != nil {
								t.Fatal(err)
							}
							pipe := f.client.redis.TxPipeline()
							pipe.Rename(ctx, prefix+"simple:"+f.task.Domain, prefix+"browser:"+f.task.Domain)
							pipe.ZRem(ctx, "ready:simple"+tier, f.task.Domain)
							pipe.ZAdd(ctx, "ready:browser"+tier, redis.Z{Member: f.task.Domain, Score: score})
							if _, err := pipe.Exec(ctx); err != nil {
								t.Fatal(err)
							}
							f.task.Worker = Browser
						}
						enabled, status := false, "active"
						if mode == "blocked" {
							enabled, status = true, "blocked"
						}
						if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=$2,board_status=$3 WHERE id=$1::uuid", f.task.ID, enabled, status); err != nil {
							t.Fatal(err)
						}
						if kind == Scrape {
							// Registry retirement removes the board cache while old
							// posting members can remain in either worker lane.
							if err := f.client.redis.Del(ctx, "board:"+f.task.ID).Err(); err != nil {
								t.Fatal(err)
							}
							if mode == "disabled" {
								if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,next_scrape_at=NULL WHERE id=$1::uuid", f.task.ID); err != nil {
									t.Fatal(err)
								}
							}
						}
						oldToken := strings.Repeat("e", 32)
						if oldFence {
							if _, err := f.observer.Exec(ctx, `INSERT INTO ordinary_worker_write_fence
 (task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state)
 VALUES($1,$2::uuid,$2::uuid,$3,$4,$5,'active')`, string(kind), f.task.ID, f.epoch, oldToken, strings.Repeat("a", 64)); err != nil {
								t.Fatal(err)
							}
						}
						before := coldCanonicalSnapshot(t, f)
						claim, err := a.Claim(ctx, worker)
						if claim == nil || !errors.Is(err, ErrAuthorityLost) {
							t.Fatal("ineligible board acquired default execution authority", err)
						}
						if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error {
							t.Fatal("refused claim invoked a canonical writer")
							return nil
						}); err == nil {
							t.Fatal("refused claim retained write authority")
						}
						var fences int
						var tokenUnchanged bool
						if err := f.observer.QueryRow(ctx, `SELECT count(*),coalesce(bool_and(claim_token=$2),true)
 FROM ordinary_worker_write_fence WHERE task_kind=$1 AND task_id=$3::uuid`, string(kind), oldToken, f.task.ID).Scan(&fences, &tokenUnchanged); err != nil || fences != map[bool]int{false: 0, true: 1}[oldFence] || !tokenUnchanged {
							t.Fatal("refusal created or replaced a productive fence", err)
						}
						if coldCanonicalSnapshot(t, f) != before || f.client.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 1 || !f.client.redis.HExists(ctx, "inflight_tokens:"+string(worker), inflight(f.task)).Val() {
							t.Fatal("refusal changed canonical state or lost the raw attempt")
						}
						// Exercise the original final-retry disposition rather than
						// fabricating acknowledgement or clearing its retry budget.
						if err := f.client.redis.HSet(ctx, "inflight_strikes:"+string(worker), inflight(f.task), "2").Err(); err != nil {
							t.Fatal(err)
						}
						if err := f.client.redis.ZAdd(ctx, "inflight:"+string(worker), redis.Z{Member: inflight(f.task), Score: 0}).Err(); err != nil {
							t.Fatal(err)
						}
						if _, err := guardedReap(t, f); err != nil {
							t.Fatal("original guarded recovery failed", err)
						}
						if f.client.redis.ZScore(ctx, "deadletter:"+string(worker), inflight(f.task)).Err() != nil || f.client.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 0 || coldCanonicalSnapshot(t, f) != before {
							t.Fatal("ineligible attempt lost its bounded recovery disposition")
						}
					})
				}
			}
		}
	}
}

func TestRealDefaultRecoverableBoardStatesStillActivate(t *testing.T) {
	for _, status := range []string{"active", "suspect", "quarantined", "gone_pending", "gone"} {
		t.Run(status, func(t *testing.T) {
			f := greenhouseAuthorityFixture(t)
			ctx := context.Background()
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_status=$2 WHERE id=$1::uuid", f.task.ID, status); err != nil {
				t.Fatal(err)
			}
			a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(a.Close)
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("recoverable enabled board lost execution authority", err)
			}
			if _, err := a.ObserveGreenhouseMonitor(ctx, f.task.ID); err != nil {
				t.Fatal("recoverable enabled board lost its compiled profile", err)
			}
		})
	}
}
