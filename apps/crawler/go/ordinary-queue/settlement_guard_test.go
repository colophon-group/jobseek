package queue

import (
	"context"
	"errors"
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealNativeSettlementPreflightsCorruptStateBeforeAnyEffect(t *testing.T) {
	for _, mode := range []string{"ready0", "ready1", "ready2", "strikes", "monitors", "scrapes", "ft_monitors", "ft_scrapes", "rate_type", "rate_value", "rotation_value", "repair_value", "queue_value"} {
		t.Run(mode, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			seed, _ := seedTask(t, c, Monitor, Simple)
			task, err := c.ClaimFenced(ctx, Simple)
			if err != nil || task == nil {
				t.Fatal("guard fixture missing tokenized claim", err)
			}
			member := "monitor|" + task.Domain + "|" + task.ID
			var injectErr error
			switch mode {
			case "ready0", "ready1", "ready2":
				injectErr = c.redis.Set(ctx, "ready:simple:"+mode[len(mode)-1:], "corrupt", 0).Err()
			case "strikes":
				injectErr = c.redis.Set(ctx, "inflight_strikes:simple", "corrupt", 0).Err()
			case "monitors", "scrapes", "ft_monitors", "ft_scrapes":
				injectErr = c.redis.Set(ctx, mode+"_simple:"+task.Domain, "corrupt", 0).Err()
			case "rate_type":
				if err := c.redis.Del(ctx, "ratelimit:"+task.Domain).Err(); err != nil {
					t.Fatal(err)
				}
				injectErr = c.redis.LPush(ctx, "ratelimit:"+task.Domain, "corrupt").Err()
			case "rate_value":
				injectErr = c.redis.Set(ctx, "ratelimit:"+task.Domain, "not-a-number", 0).Err()
			case "rotation_value":
				injectErr = c.redis.ZAdd(ctx, "ready:rotation:simple", redis.Z{Score: math.Inf(1), Member: task.Domain}).Err()
			case "repair_value":
				injectErr = c.redis.HSet(ctx, "monitor_repair_due:simple", member, "not-a-number").Err()
			case "queue_value":
				injectErr = c.redis.ZAdd(ctx, "monitors_simple:"+task.Domain, redis.Z{Score: math.Inf(1), Member: "future-sibling"}).Err()
			}
			if injectErr != nil {
				t.Fatal(injectErr)
			}
			keys := []string{"board:" + seed.ID, "inflight:simple", "inflight_tokens:simple", "inflight_strikes:simple", "ready:simple:0", "ready:simple:1", "ready:simple:2", "ready:rotation:simple", "monitor_repair_due:simple"}
			for _, prefix := range []string{"monitors_", "scrapes_", "ft_monitors_", "ft_scrapes_"} {
				keys = append(keys, prefix+"simple:"+task.Domain)
			}
			if mode == "rate_type" || mode == "rate_value" {
				keys = append(keys, "ratelimit:"+task.Domain)
			}
			snapshot := func() map[string]string {
				state := map[string]string{}
				for _, key := range keys {
					value, err := c.redis.Dump(ctx, key).Result()
					if err != nil && err != redis.Nil {
						t.Fatal(err)
					}
					state[key] = value
				}
				return state
			}
			before := snapshot()
			host := "learned.example.com"
			if accepted, err := c.rescheduleHost(ctx, task, seconds(time.Now().Add(time.Hour)), &host); accepted || !errors.Is(err, ErrObservation) {
				t.Fatal("corrupt settlement accepted")
			}
			if !reflect.DeepEqual(before, snapshot()) {
				t.Fatal("rejected script partially mutated queue/lease/host state")
			}
		})
	}
}
