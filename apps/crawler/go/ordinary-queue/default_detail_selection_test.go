package queue

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

func seedCapacityDetail(t *testing.T, c *Client, worker WorkerType, domain, id string, now float64) *Task {
	t.Helper()
	ctx := context.Background()
	task := &Task{Worker: worker, Kind: Scrape, Domain: domain, ID: id}
	if err := c.redis.HSet(ctx, "scrape:"+id, map[string]string{"domain": domain, "board_id": id, "source_url": "https://" + domain + "/held", "crawler_type": "greenhouse"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "scrapes_"+string(worker)+":"+domain, redis.Z{Score: now - 1, Member: id}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ready:"+string(worker)+":2", redis.Z{Score: now - 1, Member: domain}).Err(); err != nil {
		t.Fatal(err)
	}
	return task
}

func TestBusyMonitorCapacityAllowsIndependentRecurringDetails(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, streak := range []string{"0", "7"} {
			t.Run(string(worker)+"/"+streak, func(t *testing.T) {
				c := privateRedis(t)
				ctx := context.Background()
				monitor, now := seedTask(t, c, Monitor, worker)
				detail := seedCapacityDetail(t, c, worker, "independent.invalid", "00000000-0000-4000-8000-000000000002", now)
				streakKey := "claim:recurring-monitor-streak:" + string(worker)
				if err := c.redis.Set(ctx, streakKey, streak, 0).Err(); err != nil {
					t.Fatal(err)
				}
				claim, err := c.ClaimFencedDetails(ctx, worker)
				if err != nil || claim == nil || claim.ID != detail.ID || !claim.Fenced() {
					t.Fatal("spare discovery capacity could not claim independent detail work", err)
				}
				if c.redis.Get(ctx, streakKey).Val() != streak {
					t.Fatal("independent capacity claim spent a monitor fairness turn")
				}
				if c.redis.ZScore(ctx, "monitors_"+string(worker)+":"+monitor.Domain, monitor.ID).Val() != now-1 || c.redis.Exists(ctx, "ratelimit:"+monitor.Domain).Val() != 0 {
					t.Fatal("independent detail changed the pending monitor or its host throttle")
				}
				if c.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 1 {
					t.Fatal("capacity claim acquired a monitor lease")
				}
			})
		}
	}
}

func TestBusyMonitorCapacityScansPastBlockedDomainsWithinExistingBudget(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		t.Run(string(worker), func(t *testing.T) {
			c := privateRedis(t)
			c.settings.MaxDomains = 2
			ctx := context.Background()
			_, now := seedTask(t, c, Monitor, worker)
			for i := 0; i < 2; i++ {
				domain := fmt.Sprintf("blocked-%d.invalid", i)
				id := fmt.Sprintf("00000000-0000-4000-8000-%012d", 10+i)
				seedCapacityDetail(t, c, worker, domain, id, now-10)
				monitorID := fmt.Sprintf("00000000-0000-4000-8000-%012d", 20+i)
				if err := c.redis.HSet(ctx, "board:"+monitorID, map[string]string{"domain": domain, "crawler_type": "greenhouse", "source_url": "https://" + domain + "/held"}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "monitors_"+string(worker)+":"+domain, redis.Z{Score: now - 20, Member: monitorID}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "ready:"+string(worker)+":1", redis.Z{Score: now - 20, Member: domain}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			detail := seedCapacityDetail(t, c, worker, "independent.invalid", "00000000-0000-4000-8000-000000000002", now)
			if claim, err := c.ClaimFencedDetails(ctx, worker); err != nil || claim != nil {
				t.Fatal("capacity scan crossed its bounded candidate budget", err)
			}
			cursorKey := "claim:detail-capacity-cursor:" + string(worker)
			if c.redis.Get(ctx, cursorKey).Val() != "2" {
				t.Fatal("bounded capacity scan did not retain its continuation")
			}
			claim, err := c.ClaimFencedDetails(ctx, worker)
			if err != nil || claim == nil || claim.ID != detail.ID {
				t.Fatal("blocked monitor domains hid runnable independent detail work", err)
			}
			if c.redis.ZCard(ctx, "inflight:"+string(worker)).Val() != 1 {
				t.Fatal("blocked domain scan consumed a monitor or extra detail")
			}
			if c.redis.Exists(ctx, cursorKey).Val() != 0 {
				t.Fatal("successful capacity claim retained a stale cursor")
			}
		})
	}
}

func TestDetailCapacityCursorRejectsCorruptionBeforeAnyPop(t *testing.T) {
	for _, value := range []string{"hash", "-1", "NaN", "+inf", "0.5", "9007199254740992", "invalid"} {
		t.Run(value, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			detail, now := seedTask(t, c, Scrape, Simple)
			// Even first-time work must remain untouched on invalid cursor state.
			if err := c.redis.ZAdd(ctx, "ft_scrapes_simple:"+detail.Domain, redis.Z{Score: now - 1, Member: detail.ID}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.redis.ZAdd(ctx, "ready:simple:0", redis.Z{Score: now - 1, Member: detail.Domain}).Err(); err != nil {
				t.Fatal(err)
			}
			key := "claim:detail-capacity-cursor:simple"
			var err error
			if value == "hash" {
				err = c.redis.HSet(ctx, key, "invalid", "fixture").Err()
			} else {
				err = c.redis.Set(ctx, key, value, 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, c)
			if claim, err := c.ClaimFencedDetails(ctx, Simple); err == nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("corrupt capacity cursor changed queue state")
			}
		})
	}
}

func TestDetailCapacityPreservesStrictFirstTimePriority(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	monitor, now := seedTask(t, c, Monitor, Simple)
	seedCapacityDetail(t, c, Simple, "independent.invalid", "00000000-0000-4000-8000-000000000002", now)
	if err := c.redis.ZRem(ctx, "monitors_simple:"+monitor.Domain, monitor.ID).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZRem(ctx, "ready:simple:1", monitor.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ft_monitors_simple:"+monitor.Domain, redis.Z{Score: now - 1, Member: monitor.ID}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ready:simple:0", redis.Z{Score: now - 1, Member: monitor.Domain}).Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, c)
	if claim, err := c.ClaimFencedDetails(ctx, Simple); err != nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
		t.Fatal("capacity claim bypassed first-time monitor priority", err)
	}
}

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
