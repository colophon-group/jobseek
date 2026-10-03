package queue

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestRealRedisInstanceIdentityIsReadOnlyStableAndConnectionOwned(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	before, err := c.redis.DBSize(ctx).Result()
	if err != nil {
		t.Fatal("private Redis census")
	}
	a, err := c.RedisInstanceSHA256(ctx)
	if err != nil || !ownershipSHA256.MatchString(a) {
		t.Fatal("real Redis incarnation", err)
	}
	b, err := c.RedisInstanceSHA256(ctx)
	if err != nil || a != b {
		t.Fatal("incarnation observation unstable", err)
	}
	after, err := c.redis.DBSize(ctx).Result()
	if err != nil || before != after {
		t.Fatal("incarnation observation mutated keyspace")
	}
	if _, err := (&Client{}).RedisInstanceSHA256(ctx); !errors.Is(err, ErrConfiguration) {
		t.Fatal("empty client identity adopted")
	}
	if strings.Contains(a, "redis") || len(a) != 64 {
		t.Fatal("private connection identity disclosed")
	}
}
