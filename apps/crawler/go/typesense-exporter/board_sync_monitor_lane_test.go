package main

import (
	"context"
	"github.com/redis/go-redis/v9"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestBoardSyncRetiresOppositeMonitorLaneWithoutResettingCadence(t *testing.T) {
	for _, browser := range []bool{false, true} {
		name := "http"
		if browser {
			name = "browser"
		}
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			c := reaperRedisFixture(t, ctx)
			selected, other, flag := "simple", "browser", "0"
			if browser {
				selected, other, flag = other, selected, "1"
			}
			domain, id := "example.test", "board-fixture"
			for key, values := range map[string][]redis.Z{
				"monitors_" + selected + ":" + domain: {{Member: id, Score: 1500}},
				"monitors_" + other + ":" + domain:    {{Member: id, Score: 900}, {Member: "other-monitor", Score: 1200}},
				"ft_monitors_" + other + ":" + domain: {{Member: id, Score: 800}},
				"scrapes_" + other + ":" + domain:     {{Member: "other-scrape", Score: 1250}},
				"ready:" + other + ":0":               {{Member: domain, Score: 800}},
				"ready:rotation:" + other:             {{Member: domain, Score: 1500}},
			} {
				if err := c.ZAdd(ctx, key, values...).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if err := c.Set(ctx, "ratelimit:"+domain, "1400", 0).Err(); err != nil {
				t.Fatal(err)
			}
			input := boardSyncInput{Schedules: []boardSyncSchedule{{Domain: domain, BoardID: id, NextCheckAt: 1100, Config: map[string]string{"monitor_needs_browser": flag, "retained": "yes"}, Browser: browser, FirstTime: true}}}
			apply := func() {
				t.Helper()
				if err := applyBoardSync(ctx, c, input, deadletterDelays{Default: 2}, func() time.Time { return time.Unix(1000, 0) }); err != nil {
					t.Fatal(err)
				}
			}
			apply()
			for _, prefix := range []string{"ft_monitors_", "monitors_"} {
				if c.ZScore(ctx, prefix+other+":"+domain, id).Err() != redis.Nil {
					t.Fatal("stale opposite monitor remains")
				}
			}
			if c.ZScore(ctx, "monitors_"+selected+":"+domain, id).Val() != 1500 || c.ZScore(ctx, "ft_monitors_"+selected+":"+domain, id).Err() != redis.Nil {
				t.Fatal("selected cadence/tier reset")
			}
			if c.ZScore(ctx, "ready:"+other+":0", domain).Err() != redis.Nil || c.ZScore(ctx, "ready:"+other+":1", domain).Val() != 1400 || c.ZScore(ctx, "ready:"+other+":2", domain).Val() != 1500 {
				t.Fatal("unrelated priority or rate/rotation floors changed")
			}
			if c.HGet(ctx, "board:"+id, "monitor_needs_browser").Val() != flag || c.HGet(ctx, "board:"+id, "retained").Val() != "yes" {
				t.Fatal("cache publication changed")
			}
			before, err := snapshotReaperKeys(ctx, c)
			if err != nil {
				t.Fatal(err)
			}
			apply()
			after, err := snapshotReaperKeys(ctx, c)
			if err != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("not idempotent", err)
			}
		})
	}
}

func TestMonitorLaneRepairPreservesSelectedInflightAndMixedOwnership(t *testing.T) {
	for _, owner := range []string{"default", "ordinary", "B0", "B0-producer"} {
		t.Run(owner, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			c := reaperRedisFixture(t, ctx)
			domain, id := "example.test", "board-fixture"
			member := "monitor|" + domain + "|" + id
			if err := c.HSet(ctx, "board:"+id, map[string]string{"domain": domain, "monitor_needs_browser": "0"}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.ZAdd(ctx, "monitors_browser:"+domain, redis.Z{Member: id, Score: 1200}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: 1800}).Err(); err != nil {
				t.Fatal(err)
			}
			token := strings.Repeat("a", 32)
			if err := c.HSet(ctx, "inflight_tokens:simple", member, token).Err(); err != nil {
				t.Fatal(err)
			}
			if owner == "ordinary" {
				if err := c.Set(ctx, "ordinary:ownership:active", "existing-owner", 0).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if owner == "B0" {
				if err := c.HSet(ctx, "lightpanda-b0:legacy-guard", "existing-task", "existing-owner").Err(); err != nil {
					t.Fatal(err)
				}
			}
			if owner == "B0-producer" {
				if err := c.HSet(ctx, "lightpanda-b0:producer-owner", "existing-owner", "retained").Err(); err != nil {
					t.Fatal(err)
				}
			}
			before, err := snapshotReaperKeys(ctx, c)
			if err != nil {
				t.Fatal(err)
			}
			n, err := c.Eval(ctx, boardSyncMonitorLaneLua, []string{}, "simple", domain, id).Int()
			if err != nil {
				t.Fatal(err)
			}
			if c.ZScore(ctx, "inflight:simple", member).Val() != 1800 || c.HGet(ctx, "inflight_tokens:simple", member).Val() != token || c.ZCard(ctx, "monitors_simple:"+domain).Val() != 0 {
				t.Fatal("selected inflight changed or duplicated")
			}
			if owner == "default" {
				if n != 1 || c.ZScore(ctx, "monitors_browser:"+domain, id).Err() != redis.Nil {
					t.Fatal("default duplicate not retired")
				}
			} else {
				after, err := snapshotReaperKeys(ctx, c)
				if err != nil || n != 0 || !reflect.DeepEqual(before, after) {
					t.Fatal("mixed ownership routing changed", err)
				}
			}
		})
	}
}

func TestMonitorLaneRepairRefusesBeforeEffects(t *testing.T) {
	for _, kind := range []string{"inflight", "orphan-token", "missing-selected", "selected-orphan-token", "stale-cache", "corrupt-ready", "corrupt-rate", "corrupt-guard"} {
		t.Run(kind, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			c := reaperRedisFixture(t, ctx)
			domain, id := "example.test", "board-fixture"
			member := "monitor|" + domain + "|" + id
			if err := c.HSet(ctx, "board:"+id, map[string]string{"domain": domain, "monitor_needs_browser": "0"}).Err(); err != nil {
				t.Fatal(err)
			}
			for _, worker := range []string{"simple", "browser"} {
				if err := c.ZAdd(ctx, "monitors_"+worker+":"+domain, redis.Z{Member: id, Score: 1200}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			var err error
			switch kind {
			case "inflight":
				err = c.ZAdd(ctx, "inflight:browser", redis.Z{Member: member, Score: 1800}).Err()
			case "orphan-token":
				err = c.HSet(ctx, "inflight_tokens:browser", member, strings.Repeat("a", 32)).Err()
			case "missing-selected":
				err = c.Del(ctx, "monitors_simple:"+domain).Err()
			case "selected-orphan-token":
				if err = c.Del(ctx, "monitors_simple:"+domain).Err(); err == nil {
					err = c.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: 1800}).Err()
				}
			case "stale-cache":
				err = c.HSet(ctx, "board:"+id, "monitor_needs_browser", "1").Err()
			case "corrupt-ready":
				err = c.Set(ctx, "ready:browser:1", "corrupt", 0).Err()
			case "corrupt-rate":
				err = c.Set(ctx, "ratelimit:"+domain, "not-a-deadline", 0).Err()
			case "corrupt-guard":
				err = c.Set(ctx, "lightpanda-b0:legacy-guard", "corrupt", 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, err := snapshotReaperKeys(ctx, c)
			if err != nil {
				t.Fatal(err)
			}
			if c.Eval(ctx, boardSyncMonitorLaneLua, []string{}, "simple", domain, id).Err() == nil {
				t.Fatal("unsafe repair accepted")
			}
			after, err := snapshotReaperKeys(ctx, c)
			if err != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("refusal changed state", err)
			}
		})
	}
}

func TestMonitorLaneGuardFindsInflightWithoutQueuedDuplicate(t *testing.T) {
	for _, mode := range []string{"lease", "orphan-token", "mixed-owner"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			c := reaperRedisFixture(t, ctx)
			domain, id := "example.test", "board-fixture"
			member := "monitor|" + domain + "|" + id
			if err := c.HSet(ctx, "board:"+id, "domain", domain, "monitor_needs_browser", "0").Err(); err != nil {
				t.Fatal(err)
			}
			if mode == "orphan-token" {
				if err := c.HSet(ctx, "inflight_tokens:browser", member, strings.Repeat("a", 32)).Err(); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := c.ZAdd(ctx, "inflight:browser", redis.Z{Member: member, Score: 1800}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "mixed-owner" {
				if err := c.Set(ctx, "ordinary:ownership:active", "owner", 0).Err(); err != nil {
					t.Fatal(err)
				}
			}
			before, err := snapshotReaperKeys(ctx, c)
			if err != nil {
				t.Fatal(err)
			}
			n, err := c.Eval(ctx, boardSyncMonitorLaneLua, nil, "simple", domain, id).Int()
			if mode == "mixed-owner" {
				if err != nil || n != 0 {
					t.Fatal("mixed routing owner changed", err)
				}
			} else if err == nil {
				t.Fatal("wrong-lane attempt missed without queued duplicate")
			}
			after, e := snapshotReaperKeys(ctx, c)
			if e != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("guard had effects", e)
			}
		})
	}
}
