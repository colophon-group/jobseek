//go:build integration

package main

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"strings"
	"testing"
)

func TestRealFractionalEpochLegacyActivation(t *testing.T) {
	for _, score := range []float64{1791836490.000193, 1791836490.000790, 1791836490.123456, 1791836490.999999, 1791836490.000001} {
		t.Run(fmt.Sprint(score), func(t *testing.T) {
			client, q, owner := integrationRedisQueue(t)
			due := int64(math.Ceil(score * 1000))
			task := integrationTask(t, "fractional-epoch", 1, due)
			integrationSeedLegacy(t, client, task, false, score)
			r, e := q.activateLegacy(context.Background(), &task, due, integrationLegacyConfig(task), "", true, false, owner)
			if e != nil {
				t.Fatal("exact fractional schedule rejected", r, e)
			}
			record, e := q.inspect(context.Background(), task.Envelope.TaskID)
			if e != nil || record == nil || record.State != "ready" {
				t.Fatal("activation state differs", e)
			}
			guard, e := client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
			if e != nil {
				t.Fatal(e)
			}
			parts := strings.Split(guard, "|")
			got, e := strconv.ParseFloat(parts[len(parts)-1], 64)
			if e != nil || got != score {
				t.Fatal("rollback score lost precision", guard, e)
			}
			plan := map[string]any{task.Envelope.TaskID: map[string]any{
				"action": "schedule", "domain": task.Envelope.Domain, "worker_type": "browser",
				"first_time": false, "score": parts[len(parts)-1],
				"config": map[string]any{"domain": task.Envelope.Domain, "board_id": task.Envelope.BoardID,
					"source_url": task.Envelope.SourceURL, "description_r2_hash": "", "scrape_step": "0", "scrape_interval_hours": "24"},
			}}
			_, scheduled, dropped := integrationRollbackPlan(t, q, owner, plan)
			actual, e := client.ZScore(context.Background(), "scrapes_browser:"+task.Envelope.Domain, task.Envelope.TaskID).Result()
			if e != nil || scheduled != 1 || dropped != 0 || actual != score {
				t.Fatal("supported rollback changed fractional deadline", e)
			}

		})
	}
}

func TestRealRedisScientificScheduleRejectsMalformedAndOutOfBounds(t *testing.T) {
	for _, score := range []string{"nan", "inf", "1e", "1e+", "-1e2", "1e999", "1e10", "1.2.3e4"} {
		t.Run(score, func(t *testing.T) {
			client, q, owner := integrationRedisQueue(t)
			task := integrationTask(t, "invalid-scientific-guard", 1, 0)
			integrationActivate(t, q, owner, task, 0, false)
			guard := fmt.Sprintf("%s|%s|%d|%s|%s|recurring_browser|%s", owner.Namespace, owner.Route.ShardID, owner.Route.RoutingEpoch, task.Envelope.BoardID, task.Envelope.Domain, score)
			if err := client.HSet(context.Background(), legacyGuardKey, task.Envelope.TaskID, guard).Err(); err != nil {
				t.Fatal(err)
			}
			result, err := q.activateLegacy(context.Background(), &task, 0, integrationLegacyConfig(task), "", false, false, owner)
			if err == nil || result.Reason != "guard_identity_mismatch" {
				t.Fatal("invalid numeric guard gained authority", result, err)
			}
			got, err := client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
			if err != nil || got != guard {
				t.Fatal("rejected guard mutated")
			}
		})
	}
}
