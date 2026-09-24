package main

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

var readyQueueKeys = []string{
	"ready:simple:0", "ready:simple:1", "ready:simple:2",
	"ready:browser:0", "ready:browser:1", "ready:browser:2",
}

// sampleQueueDepths preserves the twelve bounded queue gauges formerly
// sampled by Python's exporter. Failure affects telemetry, not CDC writes.
func sampleQueueDepths(ctx context.Context, client *redis.Client) (map[string]int64, error) {
	deadline, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	now := strconv.FormatFloat(float64(time.Now().UnixNano())/1e9, 'f', 6, 64)
	pipe := client.Pipeline()
	type commands struct {
		ready *redis.IntCmd
		total *redis.IntCmd
	}
	counts := make([]commands, 0, len(readyQueueKeys))
	for _, key := range readyQueueKeys {
		counts = append(counts, commands{
			ready: pipe.ZCount(deadline, key, "-inf", now),
			total: pipe.ZCard(deadline, key),
		})
	}
	if _, err := pipe.Exec(deadline); err != nil {
		return nil, fmt.Errorf("read Redis queue depths: %w", err)
	}
	depths := make(map[string]int64, len(readyQueueKeys)*2)
	for i, key := range readyQueueKeys {
		ready, err := counts[i].ready.Result()
		if err != nil {
			return nil, err
		}
		total, err := counts[i].total.Result()
		if err != nil {
			return nil, err
		}
		depths[key+":ready"] = ready
		depths[key+":total"] = total
	}
	return depths, nil
}
