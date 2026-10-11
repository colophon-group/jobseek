package queue

import (
	"context"
	"testing"
	"time"
)

func TestOrderedDetailAtomicHandoff(t *testing.T) {
	for _, corruption := range []string{"", "step", "source", "first_queue"} {
		t.Run(corruption, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			task, now := seedTask(t, c, Scrape, Simple)
			claimed, err := c.ClaimFenced(ctx, Simple)
			if err != nil || claimed == nil {
				t.Fatal("fixture claim failed", err)
			}
			first := "ft_scrapes_simple:" + task.Domain
			switch corruption {
			case "step":
				c.redis.HSet(ctx, "scrape:"+task.ID, "scrape_step", "3")
			case "source":
				c.redis.HSet(ctx, "scrape:"+task.ID, "source_url", "https://ordinary-worker.invalid/changed")
			case "first_queue":
				c.redis.Set(ctx, first, "wrongtype", 0)
			}
			step, at := int16(1), time.Unix(0, int64(now*1e9))
			ok, err := c.rescheduleDetailHandoff(ctx, claimed, now+86400, nil, nil, &step, &at, nil)
			if corruption != "" {
				if err == nil || ok {
					t.Fatal("corrupt handoff accepted")
				}
				if c.redis.HGet(ctx, "inflight_tokens:simple", inflight(task)).Val() != claimed.claimToken || c.redis.ZCard(ctx, "scrapes_simple:"+task.Domain).Val() != 0 {
					t.Fatal("refusal modified lease or recurring queue")
				}
				return
			}
			if err != nil || !ok {
				t.Fatal("handoff failed", err)
			}
			if c.redis.HGet(ctx, "scrape:"+task.ID, "scrape_step").Val() != "1" || c.redis.ZScore(ctx, first, task.ID).Val() != seconds(at) || c.redis.ZScore(ctx, "scrapes_simple:"+task.Domain, task.ID).Val() != now+86400 || c.redis.HExists(ctx, "inflight_tokens:simple", inflight(task)).Val() {
				t.Fatal("handoff effects differ")
			}
			if ok, _ := c.rescheduleDetailHandoff(ctx, claimed, now+86400, nil, nil, &step, &at, nil); ok {
				t.Fatal("old token replay accepted")
			}
			next, err := c.ClaimFenced(ctx, Simple)
			if err != nil || next == nil || next.Config["scrape_step"] != "1" || next.claimToken == claimed.claimToken {
				t.Fatal("new separately fenced step unavailable", err)
			}
		})
	}
}

func TestOrderedDetailHandoffRetainsLongConfiguredInterval(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	task, now := seedTask(t, c, Scrape, Simple)
	if err := c.redis.HSet(ctx, "scrape:"+task.ID, "scrape_interval_hours", "720").Err(); err != nil {
		t.Fatal(err)
	}
	claim, err := c.ClaimFenced(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal(err)
	}
	step, at := int16(1), time.Unix(0, int64(now*1e9))
	if ok, err := c.rescheduleDetailHandoff(ctx, claim, now+720*3600, nil, nil, &step, &at, nil); err != nil || !ok {
		t.Fatal("configured interval was narrowed", err)
	}
	if c.redis.HGet(ctx, "scrape:"+task.ID, "scrape_interval_hours").Val() != "720" {
		t.Fatal("original interval changed")
	}
}
