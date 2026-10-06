package queue

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestRealFirstOwnershipSaveWaitsForBackgroundPersistence(t *testing.T) {
	for _, mode := range []string{"acknowledged", "deadline"} {
		t.Run(mode, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			for i := 0; i < 20; i++ {
				if err := c.redis.Set(ctx, fmt.Sprintf("private-save-fixture:%d", i), "retained", 0).Err(); err != nil {
					t.Fatal(err)
				}
			}
			// This isolated Redis fixture delays its child just long enough to
			// reproduce the real SAVE refusal without a large memory workload.
			if err := c.redis.ConfigSet(ctx, "rdb-key-save-delay", "100000").Err(); err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, c)
			identity, err := c.RedisInstanceSHA256(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if err := c.redis.BgSave(ctx).Err(); err != nil {
				t.Fatal(err)
			}
			persistence, err := c.redis.Info(ctx, "persistence").Result()
			if err != nil || !strings.Contains(persistence, "rdb_bgsave_in_progress:1\r\n") {
				t.Fatal("background save fixture was not active", err)
			}
			budget := 10 * time.Second
			if mode == "deadline" {
				budget = 50 * time.Millisecond
			}
			saveCtx, cancel := context.WithTimeout(ctx, budget)
			defer cancel()
			err = firstOwnershipSave(saveCtx, c)
			if mode == "deadline" {
				if !errors.Is(err, context.DeadlineExceeded) {
					t.Fatal("busy save exceeded its deadline without refusing", err)
				}
			} else if err != nil {
				t.Fatal("explicit background-save refusal was not recovered", err)
			}
			afterIdentity, err := c.RedisInstanceSHA256(ctx)
			if err != nil || identity != afterIdentity || !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("persistence wait changed server or queue data", err)
			}
		})
	}
}
