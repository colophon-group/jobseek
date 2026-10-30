package queue

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Multiple overdue boards can share one ATS throttle. A completed throttle
// wait must not replace their older deadlines with the last claim's wall time.
func TestRealRecurringMonitorKeepsDeadlineAfterSharedThrottleExpires(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		t.Run(string(worker), func(t *testing.T) {
			c := privateRedis(t)
			c.settings.DefaultDelaySeconds = 0.01
			ctx := context.Background()
			now, err := c.clock(ctx)
			if err != nil {
				t.Fatal(err)
			}
			seed := func(id, domain string, due float64) {
				t.Helper()
				if err := c.redis.HSet(ctx, "board:"+id, map[string]string{"domain": domain, "crawler_type": "greenhouse", "board_id": id}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "monitors_"+string(worker)+":"+domain, redis.Z{Member: id, Score: due}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "ready:"+string(worker)+":1", redis.Z{Member: domain, Score: due}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			// Seed the older host last in deadline order, preserving its head.
			seed("older-second", "shared-ats.invalid", now-48*3600+1)
			seed("older-first", "shared-ats.invalid", now-48*3600)
			for i := 0; i < 12; i++ {
				seed(fmt.Sprintf("younger-%d", i), fmt.Sprintf("single-%02d.invalid", i), now-24*3600+float64(i))
			}
			first, err := c.ClaimFenced(ctx, worker)
			if err != nil || first == nil || first.ID != "older-first" {
				t.Fatal("oldest monitor was not selected initially")
			}
			if accepted, err := c.Reschedule(ctx, first, now+3600); err != nil || !accepted {
				t.Fatal("completed monitor did not retain its future deadline")
			}
			// The real Redis clock must pass the existing 10ms host throttle.
			time.Sleep(30 * time.Millisecond)
			second, err := c.ClaimFenced(ctx, worker)
			if err != nil || second == nil || second.ID != "older-second" {
				t.Fatal("expired host throttle displaced the oldest pending monitor behind newer domains")
			}
		})
	}
}

func TestRealRecurringMonitorScansPastThrottledBatchWithoutWideningBudget(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	now, err := c.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i <= c.settings.MaxDomains; i++ {
		id, domain := fmt.Sprintf("bounded-%d", i), fmt.Sprintf("bounded-%02d.invalid", i)
		due := now - 3600 + float64(i)
		if err := c.redis.HSet(ctx, "board:"+id, map[string]string{"domain": domain, "board_id": id}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "monitors_simple:"+domain, redis.Z{Member: id, Score: due}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Member: domain, Score: due}).Err(); err != nil {
			t.Fatal(err)
		}
		if i < c.settings.MaxDomains {
			if err := c.redis.Set(ctx, "ratelimit:"+domain, now+60, 0).Err(); err != nil {
				t.Fatal(err)
			}
		}
	}
	if claim, err := c.ClaimFenced(ctx, Simple); err != nil || claim != nil {
		t.Fatal("one claim exceeded its original domain candidate budget")
	}
	claim, err := c.ClaimFenced(ctx, Simple)
	if err != nil || claim == nil || claim.ID != fmt.Sprintf("bounded-%d", c.settings.MaxDomains) {
		t.Fatal("throttled oldest monitors stranded a later eligible domain")
	}
	for i := 0; i < c.settings.MaxDomains; i++ {
		if n, err := c.redis.ZCard(ctx, fmt.Sprintf("monitors_simple:bounded-%02d.invalid", i)).Result(); err != nil || n != 1 {
			t.Fatal("bounded scan removed a throttled pending monitor")
		}
	}
}

func TestRealRecurringMonitorCursorCorruptionPrecedesMutation(t *testing.T) {
	for _, value := range []string{"wrong-type", "NaN", "-1", "1.5", "9007199254740992"} {
		t.Run(value, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			task, _ := seedTask(t, c, Monitor, Simple)
			key := "claim:recurring-monitor-cursor:simple"
			if value == "wrong-type" {
				if err := c.redis.LPush(ctx, key, "invalid").Err(); err != nil {
					t.Fatal(err)
				}
			} else if err := c.redis.Set(ctx, key, value, 0).Err(); err != nil {
				t.Fatal(err)
			}
			if claim, err := c.ClaimFenced(ctx, Simple); err == nil || claim != nil {
				t.Fatal("corrupt cursor was accepted")
			}
			if n, err := c.redis.ZCard(ctx, "monitors_simple:"+task.Domain).Result(); err != nil || n != 1 {
				t.Fatal("cursor rejection removed pending work")
			}
			if n, err := c.redis.ZCard(ctx, "inflight:simple").Result(); err != nil || n != 0 {
				t.Fatal("cursor rejection created an inflight attempt")
			}
			if n, err := c.redis.Exists(ctx, "ratelimit:"+task.Domain).Result(); err != nil || n != 0 {
				t.Fatal("cursor rejection spent the host throttle")
			}
		})
	}
}

func TestRealThrottledMonitorScanStillAllowsIndependentRecurringDetail(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	now, err := c.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < c.settings.MaxDomains; i++ {
		id, domain := fmt.Sprintf("blocked-%d", i), fmt.Sprintf("blocked-%02d.invalid", i)
		if err := c.redis.HSet(ctx, "board:"+id, map[string]string{"domain": domain}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "monitors_simple:"+domain, redis.Z{Member: id, Score: now - 3600}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Member: domain, Score: now - 3600}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.Set(ctx, "ratelimit:"+domain, now+60, 0).Err(); err != nil {
			t.Fatal(err)
		}
	}
	detail, _ := seedTask(t, c, Scrape, Simple)
	if claim, err := c.ClaimFenced(ctx, Simple); err != nil || claim != nil {
		t.Fatal("throttled monitor pass bypassed its bounded priority turn")
	}
	claim, err := c.ClaimFenced(ctx, Simple)
	if err != nil || claim == nil || claim.Kind != Scrape || claim.ID != detail.ID {
		t.Fatal("exhausted throttled-monitor scan starved independent detail work")
	}
}

func TestRealMonitorCursorDoesNotSkipWorkAfterStaleMarkersDisappear(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	now, err := c.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 12; i++ {
		domain, id := fmt.Sprintf("stale-%02d.invalid", i), fmt.Sprintf("stale-detail-%d", i)
		if err := c.redis.ZAdd(ctx, "scrapes_simple:"+domain, redis.Z{Member: id, Score: now - 100}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.HSet(ctx, "scrape:"+id, map[string]string{"source_url": "https://" + domain + "/job", "board_id": "fixture"}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Member: domain, Score: now - 1000}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.redis.ZAdd(ctx, "ready:simple:2", redis.Z{Member: domain, Score: now - 100}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	monitor, _ := seedTask(t, c, Monitor, Simple)
	if claim, err := c.ClaimFenced(ctx, Simple); err != nil || claim != nil {
		t.Fatal("stale marker cleanup bypassed its bounded monitor turn")
	}
	claim, err := c.ClaimFenced(ctx, Simple)
	if err != nil || claim == nil || claim.Kind != Monitor || claim.ID != monitor.ID {
		t.Fatal("removed stale markers advanced the cursor over an eligible monitor")
	}
}
