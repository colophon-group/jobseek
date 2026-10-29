package main

import (
	"context"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestBoardSyncRetiresPreviousMonitorDomains(t *testing.T) {
	for _, browser := range []bool{false, true} {
		for _, source := range []string{"simple", "browser"} {
			t.Run(map[bool]string{false: "http", true: "browser"}[browser]+"/from-"+source, func(t *testing.T) {
				ctx := context.Background()
				c := reaperRedisFixture(t, ctx)
				selected, flag := "simple", "0"
				if browser {
					selected, flag = "browser", "1"
				}
				id, oldDomain, domain := "board-fixture", "previous.invalid", "current.invalid"
				for key, values := range map[string][]redis.Z{
					"monitors_" + selected + ":" + domain:     {{Member: id, Score: 1500}},
					"ft_monitors_" + source + ":" + oldDomain: {{Member: id, Score: 800}},
					"monitors_" + source + ":" + oldDomain:    {{Member: id, Score: 900}, {Member: "other-monitor", Score: 1200}},
					"scrapes_" + source + ":" + oldDomain:     {{Member: "other-scrape", Score: 1250}},
					"ready:" + source + ":0":                  {{Member: oldDomain, Score: 800}},
					"ready:rotation:" + source:                {{Member: oldDomain, Score: 1500}},
				} {
					if err := c.ZAdd(ctx, key, values...).Err(); err != nil {
						t.Fatal(err)
					}
				}
				if err := c.Set(ctx, "ratelimit:"+oldDomain, "1400", 0).Err(); err != nil {
					t.Fatal(err)
				}
				input := boardSyncInput{Schedules: []boardSyncSchedule{{Domain: domain, BoardID: id, Browser: browser, FirstTime: true, NextCheckAt: 1100, Config: map[string]string{"monitor_needs_browser": flag, "retained": "yes"}}}}
				apply := func() {
					t.Helper()
					if err := applyBoardSync(ctx, c, input, deadletterDelays{Default: 2}, func() time.Time { return time.Unix(1000, 0) }); err != nil {
						t.Fatal(err)
					}
				}
				apply()
				for _, prefix := range []string{"ft_monitors_", "monitors_"} {
					if c.ZScore(ctx, prefix+source+":"+oldDomain, id).Err() != redis.Nil {
						t.Fatal("previous-domain monitor remains")
					}
				}
				if c.ZScore(ctx, "monitors_"+selected+":"+domain, id).Val() != 1500 || c.ZScore(ctx, "ft_monitors_"+selected+":"+domain, id).Err() != redis.Nil || c.HGet(ctx, "board:"+id, "retained").Val() != "yes" {
					t.Fatal("canonical cadence, tier or cache changed")
				}
				if c.ZScore(ctx, "ready:"+source+":0", oldDomain).Err() != redis.Nil || c.ZScore(ctx, "ready:"+source+":1", oldDomain).Val() != 1400 || c.ZScore(ctx, "ready:"+source+":2", oldDomain).Val() != 1500 || c.ZScore(ctx, "monitors_"+source+":"+oldDomain, "other-monitor").Val() != 1200 {
					t.Fatal("unrelated source work or rate/rotation floor changed")
				}
				before, _ := snapshotReaperKeys(ctx, c)
				apply()
				after, err := snapshotReaperKeys(ctx, c)
				if err != nil || !reflect.DeepEqual(before, after) {
					t.Fatal("repeated publication changed monitor routes", err)
				}
			})
		}
	}
}

func TestPreviousMonitorDomainRetirementGuards(t *testing.T) {
	for _, mode := range []string{"selected-inflight", "source-inflight", "source-token", "missing-selected", "changed-cache", "corrupt-source", "corrupt-rate", "ordinary-owner", "B0-owner"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			c := reaperRedisFixture(t, ctx)
			id, domain, oldDomain := "board-fixture", "current.invalid", "previous.invalid"
			selectedMember, oldMember := "monitor|"+domain+"|"+id, "monitor|"+oldDomain+"|"+id
			s := boardSyncSchedule{Domain: domain, BoardID: id, Config: map[string]string{"monitor_needs_browser": "0"}}
			if err := c.HSet(ctx, "board:"+id, "domain", domain, "monitor_needs_browser", "0").Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.ZAdd(ctx, "monitors_simple:"+domain, redis.Z{Member: id, Score: 1500}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.ZAdd(ctx, "monitors_browser:"+oldDomain, redis.Z{Member: id, Score: 900}).Err(); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "selected-inflight":
				c.Del(ctx, "monitors_simple:"+domain)
				c.ZAdd(ctx, "inflight:simple", redis.Z{Member: selectedMember, Score: 1800})
				c.HSet(ctx, "inflight_tokens:simple", selectedMember, strings.Repeat("a", 32))
			case "source-inflight":
				c.ZAdd(ctx, "inflight:browser", redis.Z{Member: oldMember, Score: 1800})
			case "source-token":
				c.HSet(ctx, "inflight_tokens:browser", oldMember, strings.Repeat("a", 32))
			case "missing-selected":
				c.Del(ctx, "monitors_simple:"+domain)
			case "changed-cache":
				c.HSet(ctx, "board:"+id, "domain", "changed.invalid")
			case "corrupt-source":
				c.Del(ctx, "monitors_browser:"+oldDomain)
				c.Set(ctx, "monitors_browser:"+oldDomain, "corrupt", 0)
			case "corrupt-rate":
				c.Set(ctx, "ratelimit:"+oldDomain, "NaN", 0)
			case "ordinary-owner":
				c.Set(ctx, "ordinary:ownership:active", "retained", 0)
			case "B0-owner":
				c.HSet(ctx, "lightpanda-b0:legacy-guard", "owned", "retained")
			}
			sha, err := c.ScriptLoad(ctx, boardSyncMonitorLaneLua).Result()
			if err != nil {
				t.Fatal(err)
			}
			before, _ := snapshotReaperKeys(ctx, c)
			err = retirePreviousMonitorRoutes(ctx, c, []boardSyncSchedule{s}, sha)
			after, snapshotErr := snapshotReaperKeys(ctx, c)
			if snapshotErr != nil {
				t.Fatal(snapshotErr)
			}
			if mode == "selected-inflight" {
				if err != nil || c.ZScore(ctx, "monitors_browser:"+oldDomain, id).Err() != redis.Nil || c.ZScore(ctx, "inflight:simple", selectedMember).Val() != 1800 || c.HGet(ctx, "inflight_tokens:simple", selectedMember).Val() != strings.Repeat("a", 32) {
					t.Fatal("selected current attempt changed", err)
				}
				return
			}
			owned := mode == "ordinary-owner" || mode == "B0-owner"
			if owned && err != nil || !owned && err == nil || !reflect.DeepEqual(before, after) {
				t.Fatal("refused or foreign-owned alias retirement changed state", err)
			}
		})
	}
}
