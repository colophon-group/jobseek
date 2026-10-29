package main

import (
	"context"
	"github.com/redis/go-redis/v9"
	"reflect"
	"strings"
	"testing"
)

func TestMissingScheduleLifecycle(t *testing.T) {
	for _, mode := range []string{"missing", "zero-delay", "cache", "first", "queued", "opposite-queued", "inflight", "opposite-inflight", "orphan-token", "deadletter", "strikes", "owner", "corrupt-ready", "corrupt-rate", "corrupt-board"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			c := capacityRedisFixture(t, ctx)
			id, board, domain := "detail-fixture", "board-fixture", "example.test"
			member := "scrape|" + domain + "|" + id
			if e := c.HSet(ctx, "board:"+board, "scraper_needs_browser", "0").Err(); e != nil {
				t.Fatal(e)
			}
			var e error
			switch mode {
			case "cache":
				e = c.HSet(ctx, "scrape:"+id, "scrape_step", "3", "scrape_interval_hours", "72").Err()
			case "first":
				e = c.ZAdd(ctx, "ft_scrapes_simple:"+domain, redis.Z{Member: id, Score: 11}).Err()
			case "queued":
				e = c.ZAdd(ctx, "scrapes_simple:"+domain, redis.Z{Member: id, Score: 12}).Err()
			case "opposite-queued":
				e = c.ZAdd(ctx, "scrapes_browser:"+domain, redis.Z{Member: id, Score: 13}).Err()
			case "inflight":
				e = c.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: 14}).Err()
			case "opposite-inflight":
				e = c.ZAdd(ctx, "inflight:browser", redis.Z{Member: member, Score: 15}).Err()
			case "orphan-token":
				e = c.HSet(ctx, "inflight_tokens:browser", member, strings.Repeat("a", 32)).Err()
			case "deadletter":
				e = c.ZAdd(ctx, "deadletter:simple", redis.Z{Member: member, Score: 16}).Err()
			case "strikes":
				e = c.HSet(ctx, "inflight_strikes:simple", member, "2").Err()
			case "owner":
				e = c.Set(ctx, "ordinary:ownership:active", "owner", 0).Err()
			case "corrupt-ready":
				e = c.Set(ctx, "ready:simple:1", "wrong-type", 0).Err()
			case "corrupt-rate":
				e = c.Set(ctx, "ratelimit:"+domain, "not-a-time", 0).Err()
			case "corrupt-board":
				e = c.HSet(ctx, "board:"+board, "scraper_needs_browser", "1").Err()
			}
			if e != nil {
				t.Fatal(e)
			}
			before, e := snapshotReaperKeys(ctx, c)
			if e != nil {
				t.Fatal(e)
			}
			args := []any{"simple", domain, id, "1234.5", "scrape", "0", "0", "source_url", "https://example.test/1", "board_id", board, "description_r2_hash", "fixture-retained-hash", "scrape_step", "0", "scrape_interval_hours", "72"}
			delay := "2"
			if mode == "zero-delay" {
				delay = "0"
			}
			n, e := c.EvalRO(ctx, missingScheduleProgram(), []string{"inspect", delay}, args...).Int()
			refuse := mode == "owner" || strings.HasPrefix(mode, "corrupt-")
			missing := mode == "missing" || mode == "zero-delay" || mode == "strikes"
			if refuse {
				if e == nil {
					t.Fatal("unsafe dry run accepted")
				}
			} else if e != nil || n != map[bool]int{true: 1, false: 0}[missing] {
				t.Fatal("inspection changed eligibility", n, e)
			}
			after, e2 := snapshotReaperKeys(ctx, c)
			if e2 != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("read-only inspection changed lifecycle")
			}
			n, e = c.Eval(ctx, missingScheduleProgram(), []string{"apply", delay}, args...).Int()
			if refuse {
				if e == nil {
					t.Fatal("unsafe repair accepted")
				}
				after, e2 = snapshotReaperKeys(ctx, c)
				if e2 != nil || !reflect.DeepEqual(before, after) {
					t.Fatal("refusal had effects")
				}
				return
			}
			if !missing {
				if e != nil || n != 0 {
					t.Fatal("existing lifecycle overwritten", n, e)
				}
				after, e2 = snapshotReaperKeys(ctx, c)
				if e2 != nil || !reflect.DeepEqual(before, after) {
					t.Fatal("existing cache/attempt changed")
				}
				return
			}
			if e != nil || n != 1 || c.ZScore(ctx, "scrapes_simple:"+domain, id).Val() != 1234.5 || c.HGet(ctx, "scrape:"+id, "scrape_interval_hours").Val() != "72" || c.HGet(ctx, "scrape:"+id, "scrape_step").Val() != "0" || c.Get(ctx, "delay:"+domain).Val() != delay {
				t.Fatal("missing canonical projection not restored", e)
			}
			if mode == "strikes" && c.HGet(ctx, "inflight_strikes:simple", member).Val() != "2" {
				t.Fatal("retry history reset")
			}
			before, e = snapshotReaperKeys(ctx, c)
			if e != nil {
				t.Fatal(e)
			}
			n, e = c.Eval(ctx, missingScheduleProgram(), []string{"apply", delay}, args...).Int()
			after, e2 = snapshotReaperKeys(ctx, c)
			if e != nil || e2 != nil || n != 0 || !reflect.DeepEqual(before, after) {
				t.Fatal("repair not idempotent")
			}
		})
	}
}
