package queue

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Compare all private fixture values, excluding TTL countdown. Do not print
// tokens/configuration values in failures.
func snapshot(t *testing.T, c *Client) map[string]string {
	t.Helper()
	ctx := context.Background()
	keys, err := c.redis.Keys(ctx, "*").Result()
	if err != nil {
		t.Fatal("private census failed")
	}
	out := map[string]string{}
	for _, key := range keys {
		// Hash reads can advance Redis's internal rehash and reorder DUMP
		// bytes without changing a field. Compare their logical values so
		// extra read-only cold observations cannot masquerade as mutations.
		if c.redis.Type(ctx, key).Val() == "hash" {
			fields, err := c.redis.HGetAll(ctx, key).Result()
			if err != nil {
				t.Fatal("private hash snapshot failed")
			}
			body, err := json.Marshal(fields)
			if err != nil {
				t.Fatal("private hash snapshot encoding failed")
			}
			out[key] = string(body)
			continue
		}
		value, err := c.redis.Dump(ctx, key).Result()
		if err != nil {
			t.Fatal("private snapshot failed")
		}
		out[key] = value
	}
	return out
}
func reaperSource(t *testing.T) string {
	t.Helper()
	body, err := os.ReadFile("../../src/lua/reap_expired.lua")
	if err != nil {
		t.Fatal(err)
	}
	installed, err := os.ReadFile("../typesense-exporter/lease_reaper.lua")
	if err != nil || string(body) != string(installed) {
		t.Fatal("installed reaper differs from authority")
	}
	return string(body)
}
func reap(t *testing.T, c *Client, worker WorkerType, strikes int) []any {
	t.Helper()
	now, err := c.clock(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	raw, err := c.redis.Eval(context.Background(), reaperSource(t), nil, string(worker), number(now), 10, strikes, number(now), "guarded").Slice()
	if err != nil || len(raw) != 3 {
		t.Fatal("real reaper failed")
	}
	return raw
}
func expire(t *testing.T, c *Client, task *Task) {
	t.Helper()
	now, err := c.clock(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(context.Background(), "inflight:"+string(task.Worker), redis.Z{Score: now - 1, Member: inflight(task)}).Err(); err != nil {
		t.Fatal(err)
	}
}
func rejectStale(t *testing.T, c *Client, task *Task) {
	t.Helper()
	ctx := context.Background()
	before := snapshot(t, c)
	if ok, err := c.Heartbeat(ctx, task); err != nil || ok {
		t.Fatal("stale heartbeat accepted")
	}
	if ok, err := c.Reschedule(ctx, task, task.InitialLeaseUntil+3600); err != nil || ok {
		t.Fatal("stale reschedule accepted")
	}
	if ok, err := c.Complete(ctx, task); err != nil || ok {
		t.Fatal("stale completion accepted")
	}
	if !reflect.DeepEqual(before, snapshot(t, c)) {
		t.Fatal("stale attempt mutated current state")
	}
}

func TestRealRedisExpiryReclaimAndLegacyIsolation(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, kind := range []Kind{Monitor, Scrape} {
			t.Run(string(worker)+"/"+string(kind), func(t *testing.T) {
				c := privateRedis(t)
				seedTask(t, c, kind, worker)
				ctx := context.Background()
				old, err := c.ClaimFenced(ctx, worker)
				if err != nil || old == nil || !old.Fenced() {
					t.Fatal("token claim failed")
				}
				if score, err := c.redis.ZScore(ctx, "inflight:"+string(worker), inflight(old)).Result(); err != nil || score != old.InitialLeaseUntil {
					t.Fatal("atomic deadline differs")
				}
				legacy := *old
				legacy.claimToken = ""
				rejectStale(t, c, &legacy)
				expire(t, c, old)
				rejectStale(t, c, old) // Expiry alone revokes authority before reaping.
				if counts := reap(t, c, worker, 3); !reflect.DeepEqual(counts, []any{int64(1), int64(0), int64(0)}) {
					t.Fatal("retry conservation failed")
				}
				current, err := c.ClaimFenced(ctx, worker)
				if err != nil || current == nil || current.ID != old.ID || current.claimToken == old.claimToken {
					t.Fatal("reclaim generation differs")
				}
				rejectStale(t, c, old)
				rejectStale(t, c, &legacy)
				if ok, err := c.Heartbeat(ctx, current); err != nil || !ok {
					t.Fatal("current heartbeat rejected")
				}
				next := current.InitialLeaseUntil + 3600
				if ok, err := c.Reschedule(ctx, current, next); err != nil || !ok {
					t.Fatal("current settlement rejected")
				}
				prefix := "monitors_"
				if kind == Scrape {
					prefix = "scrapes_"
				}
				if score, err := c.redis.ZScore(ctx, prefix+string(worker)+":"+current.Domain, current.ID).Result(); err != nil || score != next {
					t.Fatal("DB deadline changed")
				}
				rejectStale(t, c, current)
				if exists, err := c.redis.Exists(ctx, "inflight_tokens:"+string(worker)).Result(); err != nil || exists != 0 {
					t.Fatal("settlement retained token")
				}
			})
		}
	}
}

func TestRealRedisCompletionRepairAndReaperTokenCleanup(t *testing.T) {
	for _, path := range []string{"complete", "repair", "deadletter", "orphan", "b0", "malformed"} {
		t.Run(path, func(t *testing.T) {
			c := privateRedis(t)
			kind := Scrape
			if path == "repair" {
				kind = Monitor
			}
			task, now := seedTask(t, c, kind, Simple)
			ctx := context.Background()
			current, err := c.ClaimFenced(ctx, Simple)
			if err != nil || current == nil {
				t.Fatal("fixture claim failed")
			}
			if path == "complete" {
				if ok, err := c.Complete(ctx, current); err != nil || !ok {
					t.Fatal("completion rejected")
				}
				if exists, err := c.redis.Exists(ctx, "scrape:"+task.ID, "inflight_tokens:simple").Result(); err != nil || exists != 0 {
					t.Fatal("drained state retained")
				}
				rejectStale(t, c, current)
				return
			}
			if path == "repair" {
				if err := c.redis.HSet(ctx, "monitor_repair_due:simple", inflight(task), number(now-2)).Err(); err != nil {
					t.Fatal(err)
				}
				if ok, err := c.Complete(ctx, current); err != nil || ok {
					t.Fatal("pending repair discarded")
				}
				rejectStale(t, c, current)
			} else {
				expire(t, c, current)
			}
			if path == "orphan" {
				if err := c.redis.Del(ctx, "scrape:"+task.ID).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if path == "b0" {
				if err := c.redis.HSet(ctx, "lightpanda-b0:legacy-guard", task.ID, "fixture-b0").Err(); err != nil {
					t.Fatal(err)
				}
			}
			if path == "malformed" {
				if err := c.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 0, Member: "malformed"}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.HSet(ctx, "inflight_tokens:simple", "malformed", current.claimToken).Err(); err != nil {
					t.Fatal(err)
				}
			}
			strikes := 3
			if path == "deadletter" {
				strikes = 1
			}
			counts := reap(t, c, Simple, strikes)
			if path == "deadletter" && counts[1] != int64(1) {
				t.Fatal("deadletter conservation failed")
			}
			if path == "orphan" && counts[2] != int64(1) {
				t.Fatal("orphan conservation failed")
			}
			if path == "repair" {
				if score, err := c.redis.ZScore(ctx, "monitors_simple:"+task.Domain, task.ID).Result(); err != nil || score != now-2 {
					t.Fatal("earliest repair deadline lost")
				}
			}
			if exists, err := c.redis.Exists(ctx, "inflight_tokens:simple").Result(); err != nil || exists != 0 {
				t.Fatal("reaper retained token")
			}
			rejectStale(t, c, current)
		})
	}
}

func TestRealRedisDuplicateCannotReplaceInflightGeneration(t *testing.T) {
	for _, fenced := range []bool{false, true} {
		for _, duplicateFenced := range []bool{false, true} {
			if !fenced && !duplicateFenced {
				continue
			} // Legacy/legacy ABI remains unchanged.
			c := privateRedis(t)
			seedTask(t, c, Scrape, Simple)
			ctx := context.Background()
			var current *Task
			var err error
			if fenced {
				current, err = c.ClaimFenced(ctx, Simple)
			} else {
				current, err = c.Claim(ctx, Simple)
			}
			if err != nil || current == nil {
				t.Fatal("first claim failed")
			}
			seedTask(t, c, Scrape, Simple) // Stale producer's duplicate representation.
			var duplicate *Task
			if duplicateFenced {
				duplicate, err = c.ClaimFenced(ctx, Simple)
			} else {
				duplicate, err = c.Claim(ctx, Simple)
			}
			if err != nil || duplicate != nil {
				t.Fatal("duplicate replaced current attempt")
			}
			if score, err := c.redis.ZScore(ctx, "inflight:simple", inflight(current)).Result(); err != nil || score != current.InitialLeaseUntil {
				t.Fatal("duplicate altered lease")
			}
			if ok, err := c.Complete(ctx, current); err != nil || !ok {
				t.Fatal("duplicate damaged original authority")
			}
		}
	}
}

func TestRealRedisTokenCorruptionAndCancellationFailBeforeMutation(t *testing.T) {
	for _, operation := range []string{"claim", "heartbeat", "complete", "reschedule", "reap"} {
		t.Run(operation, func(t *testing.T) {
			c := privateRedis(t)
			seedTask(t, c, Scrape, Simple)
			ctx := context.Background()
			current, err := c.ClaimFenced(ctx, Simple)
			if err != nil || current == nil {
				t.Fatal("first claim failed")
			}
			expire(t, c, current)
			seedTask(t, c, Scrape, Simple)
			if err := c.redis.Del(ctx, "inflight_tokens:simple").Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.redis.Set(ctx, "inflight_tokens:simple", "corrupt-fixture", 0).Err(); err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, c)
			switch operation {
			case "claim":
				_, err = c.ClaimFenced(ctx, Simple)
			case "heartbeat":
				_, err = c.Heartbeat(ctx, current)
			case "complete":
				_, err = c.Complete(ctx, current)
			case "reschedule":
				_, err = c.Reschedule(ctx, current, current.InitialLeaseUntil+3600)
			case "reap":
				_, err = c.redis.Eval(ctx, reaperSource(t), nil, "simple", number(current.InitialLeaseUntil+1), 10, 3, number(current.InitialLeaseUntil+1)).Result()
			}
			if err == nil || !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("corrupt index mutated state")
			}
		})
	}
	c := privateRedis(t)
	seedTask(t, c, Scrape, Simple)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	before := snapshot(t, c)
	if _, err := c.ClaimFenced(ctx, Simple); !errors.Is(err, ErrObservation) {
		t.Fatal("cancelled claim accepted")
	}
	if !reflect.DeepEqual(before, snapshot(t, c)) {
		t.Fatal("cancelled claim changed queue")
	}
}

func TestRealRedisCurrentAttemptHeartbeatNeverShortensAndCancelledSettlement(t *testing.T) {
	c := privateRedis(t)
	seedTask(t, c, Scrape, Simple)
	ctx := context.Background()
	current, err := c.ClaimFenced(ctx, Simple)
	if err != nil || current == nil {
		t.Fatal("first claim failed")
	}
	// The worker lowers its configured TTL; its current lease remains unchanged.
	c.settings.LeaseTTL = 1
	if ok, err := c.Heartbeat(ctx, current); err != nil || !ok {
		t.Fatal("unchanged current heartbeat rejected")
	}
	if deadline, err := c.redis.ZScore(ctx, "inflight:simple", inflight(current)).Result(); err != nil || deadline != current.InitialLeaseUntil {
		t.Fatal("heartbeat shortened current authority")
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	before := snapshot(t, c)
	if _, err := c.Heartbeat(cancelled, current); !errors.Is(err, ErrObservation) {
		t.Fatal("cancelled heartbeat accepted")
	}
	if _, err := c.Complete(cancelled, current); !errors.Is(err, ErrObservation) {
		t.Fatal("cancelled completion accepted")
	}
	if _, err := c.Reschedule(cancelled, current, current.InitialLeaseUntil+3600); !errors.Is(err, ErrObservation) {
		t.Fatal("cancelled reschedule accepted")
	}
	if !reflect.DeepEqual(before, snapshot(t, c)) {
		t.Fatal("cancelled settlement changed state")
	}
}

func TestRealRedisMalformedClaimTokenFailsBeforePop(t *testing.T) {
	c := privateRedis(t)
	seedTask(t, c, Scrape, Simple)
	ctx := context.Background()
	now, err := c.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, c)
	for _, token := range []string{"short", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|"} {
		if _, err := c.claim.Run(ctx, c.redis, nil, "simple", number(now), "0", 10, "60", token).Result(); err == nil {
			t.Fatal("malformed claim token accepted")
		}
		if !reflect.DeepEqual(before, snapshot(t, c)) {
			t.Fatal("malformed token popped a task")
		}
	}
}
