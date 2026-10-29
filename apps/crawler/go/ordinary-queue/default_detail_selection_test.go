package queue

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

func TestUnplannedDetailsPreserveMonitorCapacityAndFairness(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, firstTime := range []bool{false, true} {
			t.Run(string(worker)+map[bool]string{false: "/recurring", true: "/first-time"}[firstTime], func(t *testing.T) {
				c := privateRedis(t)
				ctx := context.Background()
				monitor, now := seedTask(t, c, Monitor, worker)
				detail, _ := seedTask(t, c, Scrape, worker, "00000000-0000-4000-8000-000000000002")
				if firstTime {
					for _, task := range []*Task{monitor, detail} {
						prefix := "monitors_"
						if task.Kind == Scrape {
							prefix = "scrapes_"
						}
						key := prefix + string(worker) + ":" + task.Domain
						if err := c.redis.ZRem(ctx, key, task.ID).Err(); err != nil {
							t.Fatal(err)
						}
						if err := c.redis.ZAdd(ctx, "ft_"+key, redis.Z{Score: now - 1, Member: task.ID}).Err(); err != nil {
							t.Fatal(err)
						}
					}
					if err := c.redis.ZAdd(ctx, "ready:"+string(worker)+":0", redis.Z{Score: now - 1, Member: detail.Domain}).Err(); err != nil {
						t.Fatal(err)
					}
				} else {
					before := snapshot(t, c)
					claim, err := c.ClaimFencedDetails(ctx, worker)
					if err != nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
						t.Fatal("occupied monitor capacity spent a monitor fairness turn")
					}
					if err := c.redis.Set(ctx, "claim:recurring-monitor-streak:"+string(worker), "8", 0).Err(); err != nil {
						t.Fatal(err)
					}
				}
				claim, err := c.ClaimFencedDetails(ctx, worker)
				if err != nil || claim == nil || claim.ID != detail.ID || claim.Kind != Scrape || !claim.Fenced() {
					t.Fatal("available detail did not receive its own fenced lease")
				}
				prefix := "monitors_"
				if firstTime {
					prefix = "ft_" + prefix
				}
				if score, err := c.redis.ZScore(ctx, prefix+string(worker)+":"+monitor.Domain, monitor.ID).Result(); err != nil || score != now-1 {
					t.Fatal("detail claim changed the waiting monitor deadline")
				}
				if n := c.redis.ZCard(ctx, "inflight:"+string(worker)).Val(); n != 1 {
					t.Fatal("detail claim acquired extra leases")
				}
				if ok, err := c.Complete(ctx, claim); err != nil || !ok {
					t.Fatal("detail lease could not settle")
				}
			})
		}
	}
}

func TestUnplannedDetailsCannotBypassInstalledOwnership(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	seedTask(t, c, Scrape, Simple)
	if err := c.redis.Set(ctx, ownershipProjectionKey, "installed-fixture", 0).Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, c)
	if claim, err := c.ClaimFencedDetails(ctx, Simple); err == nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
		t.Fatal("detail filter bypassed installed ownership")
	}
}

func TestUnplannedDetailFilterRejectsMalformedBindingsBeforePop(t *testing.T) {
	for _, item := range []struct{ role, token, filter string }{{"", "", "details"}, {"", strings.Repeat("a", 32), "unknown"}, {"legacy", strings.Repeat("a", 32), "details"}} {
		t.Run(item.role+item.filter+item.token[:min(1, len(item.token))], func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			seedTask(t, c, Scrape, Simple)
			before := snapshot(t, c)
			binding := []any{item.role, "", "", "", "", "", "", "", item.filter}
			if claim, err := c.claimTaskBound(ctx, Simple, item.token, binding); err == nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("malformed detail filter changed queue state")
			}
		})
	}
}
