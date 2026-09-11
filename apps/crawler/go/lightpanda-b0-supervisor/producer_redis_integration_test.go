//go:build integration

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func integrationRedisQueue(t *testing.T) (*redis.Client, *b0Queue, producerOwnerIdentity) {
	t.Helper()
	rawURL := os.Getenv("LIGHTPANDA_B0_INTEGRATION_REDIS_URL")
	if rawURL == "" {
		t.Skip("LIGHTPANDA_B0_INTEGRATION_REDIS_URL is not configured")
	}
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	t.Cleanup(func() { _ = client.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	luaPath := "../../src/lua/lightpanda_b0_queue.lua"
	route := routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner}
	queue, err := newB0Queue(client, luaPath, "real-redis", route, "0", newMetrics())
	if err != nil {
		t.Fatal(err)
	}
	owner := producerOwnerIdentity{
		Namespace: "real-redis", Cohort: "c1", Route: route,
		BoardSlugs: []string{"browser-use-careers"},
	}
	if err := queue.initializeProducer(ctx, owner); err != nil {
		t.Fatal(err)
	}
	return client, queue, owner
}

func integrationTask(t *testing.T, taskID string, revision, initialReadyAt int64) queueTask {
	t.Helper()
	base := validQueueTask(t)
	var envelope map[string]any
	if err := json.Unmarshal([]byte(base.Payload), &envelope); err != nil {
		t.Fatal(err)
	}
	envelope["task_id"] = taskID
	envelope["config_revision"] = revision
	envelope["initial_ready_at_ms"] = initialReadyAt
	payload, err := canonicalJSON(envelope, true)
	if err != nil {
		t.Fatal(err)
	}
	task, err := decodeQueueTask(string(payload), sha256Bytes(payload), base.EnvelopeRoute())
	if err != nil {
		t.Fatal(err)
	}
	return task
}

func integrationLegacyConfig(task queueTask) string {
	return fmt.Sprintf(
		`{"board_id":%q,"description_r2_hash":"","domain":%q,"scrape_interval_hours":"24","scrape_step":"0","source_url":%q}`,
		task.Envelope.BoardID, task.Envelope.Domain, task.Envelope.SourceURL,
	)
}

func integrationActivate(t *testing.T, queue *b0Queue, owner producerOwnerIdentity, task queueTask, readyAt int64, firstTime bool) transition {
	t.Helper()
	result, err := queue.activateLegacy(
		context.Background(), &task, readyAt, integrationLegacyConfig(task), "", false, firstTime, owner,
	)
	if err != nil {
		t.Fatalf("activation failed: %#v %v", result, err)
	}
	return result
}

func integrationRecord(t *testing.T, client *redis.Client, queue *b0Queue, taskID string) map[string]any {
	t.Helper()
	raw, err := client.HGet(context.Background(), queue.keys[1], taskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

type claimantTransitionProducerQueue struct {
	producerQueue
	queue           *b0Queue
	current         *lease
	transitioned    bool
	transitionError error
	activationCalls int
}

func (q *claimantTransitionProducerQueue) activateLegacy(
	ctx context.Context,
	task *queueTask,
	readyAtMS int64,
	legacyConfig string,
	previousPayloadSHA256 string,
	operatorTransfer bool,
	firstTime bool,
	owner producerOwnerIdentity,
) (transition, error) {
	q.activationCalls++
	if !q.transitioned {
		q.transitioned = true
		q.transitionError = q.queue.terminal(ctx, q.current, nil)
	}
	if q.transitionError != nil {
		return transition{}, q.transitionError
	}
	return q.queue.activateLegacy(
		ctx, task, readyAtMS, legacyConfig, previousPayloadSHA256,
		operatorTransfer, firstTime, owner,
	)
}

func TestRealRedisDuplicateScheduleIntentIsLexicographicallyConserved(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	task := integrationTask(t, "schedule-intent", 1, 1000)
	integrationActivate(t, queue, owner, task, 1000, false)
	integrationActivate(t, queue, owner, task, 0, false)
	if score, err := client.ZScore(context.Background(), queue.keys[2], task.Envelope.TaskID).Result(); err != nil || score != 0 {
		t.Fatalf("earlier recurring duplicate was lost: score=%v err=%v", score, err)
	}
	guard, err := client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
	if err != nil || !strings.HasSuffix(guard, "|recurring_browser|0.000") {
		t.Fatalf("earlier recurring intent became a hybrid: %q %v", guard, err)
	}
	integrationActivate(t, queue, owner, task, 0, true)
	guard, err = client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
	if err != nil || !strings.HasSuffix(guard, "|ft_browser|0.000") {
		t.Fatalf("equal first-time duplicate did not win: %q %v", guard, err)
	}
	integrationActivate(t, queue, owner, task, 800, false)
	guard, err = client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
	if err != nil || !strings.HasSuffix(guard, "|ft_browser|0.000") {
		t.Fatalf("later recurring duplicate replaced earlier intent: %q %v", guard, err)
	}
}

func TestRealRedisLuaRejectsNonzeroExternalFirstTime(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	task := integrationTask(t, "invalid-first-time", 1, 500)
	result, err := queue.callWithProducer(
		context.Background(), "activate_legacy", &task, "", 0, 500, 0, 0,
		"", integrationLegacyConfig(task), false, true, owner,
	)
	if err != nil || result.Decision != "not_current" || result.Reason != "invalid_task_envelope" {
		t.Fatalf("Lua accepted nonzero external first-time intent: %#v %v", result, err)
	}
	if exists, err := client.HExists(context.Background(), queue.keys[1], task.Envelope.TaskID).Result(); err != nil || exists {
		t.Fatalf("invalid first-time intent mutated queue: exists=%t err=%v", exists, err)
	}
}

func TestRealRedisSuccessfulRescheduleBecomesRecurringButShutdownPreservesFirstTime(t *testing.T) {
	for _, test := range []struct {
		name       string
		settle     func(*b0Queue, *lease, int64) error
		wantSuffix string
	}{
		{
			name: "successful-processing", wantSuffix: "|recurring_browser|5.000",
			settle: func(queue *b0Queue, current *lease, readyAt int64) error {
				return queue.terminal(context.Background(), current, &readyAt)
			},
		},
		{
			name: "shutdown-release", wantSuffix: "|ft_browser|5.000",
			settle: func(queue *b0Queue, current *lease, readyAt int64) error {
				return queue.release(context.Background(), current, readyAt)
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			client, queue, owner := integrationRedisQueue(t)
			task := integrationTask(t, "reschedule-kind", 1, 0)
			integrationActivate(t, queue, owner, task, 0, true)
			current, _, err := queue.claim(context.Background(), time.Minute)
			if err != nil || current == nil {
				t.Fatalf("claim failed: %#v %v", current, err)
			}
			if err := test.settle(queue, current, 5000); err != nil {
				t.Fatal(err)
			}
			guard, err := client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
			if err != nil || !strings.HasSuffix(guard, test.wantSuffix) {
				t.Fatalf("schedule kind mismatch: %q %v", guard, err)
			}
		})
	}
}

func TestRealRedisClaimantTransitionConflictRetriesExactlyOnce(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	producer.queue, producer.route, producer.owner = queue, owner.Route, owner
	request := validProducerRequest()
	prepared, err := producer.prepare(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	integrationActivate(t, queue, owner, prepared.task, prepared.requestedReadyAtMS, false)
	current, _, err := queue.claim(context.Background(), time.Minute)
	if err != nil || current == nil {
		t.Fatalf("claim failed: %#v %v", current, err)
	}
	tracingQueue := &claimantTransitionProducerQueue{producerQueue: queue, queue: queue, current: current}
	producer.queue = tracingQueue
	request.Operation, request.OperatorTransfer = "enqueue", false
	retried, result, err := producer.enqueue(context.Background(), request)
	if err != nil || result.Reason != "reactivated" || tracingQueue.activationCalls != 2 {
		t.Fatalf("bounded claimant-transition retry failed: %#v %#v calls=%d err=%v", retried, result, tracingQueue.activationCalls, err)
	}
	stored, err := queue.inspect(context.Background(), request.PostingID)
	if err != nil || stored == nil || stored.State != "ready" || stored.Task.Envelope.ConfigRevision != 2 {
		t.Fatalf("claimant-transition retry produced the wrong revision/state: %#v %v", stored, err)
	}
	if occupancy, err := queue.lifetimeOccupancy(context.Background()); err != nil || occupancy != 1 {
		t.Fatalf("retry changed lifetime occupancy: %d %v", occupancy, err)
	}
	if _, err := client.HGet(context.Background(), legacyGuardKey, request.PostingID).Result(); err != nil {
		t.Fatalf("retry lost rollback guard: %v", err)
	}
}

func TestRealRedisInflightEnqueueSurvivesCompleteRescheduleFailAndReap(t *testing.T) {
	tests := []struct {
		name       string
		settle     func(*testing.T, *b0Queue, *lease)
		wantSuffix string
	}{
		{
			name: "complete", wantSuffix: "|ft_browser|0.000",
			settle: func(t *testing.T, q *b0Queue, current *lease) {
				t.Helper()
				if err := q.terminal(context.Background(), current, nil); err != nil {
					t.Fatal(err)
				}
			},
		},
		{
			name: "reschedule", wantSuffix: "|ft_browser|0.000",
			settle: func(t *testing.T, q *b0Queue, current *lease) {
				t.Helper()
				ready := int64(5000)
				if err := q.terminal(context.Background(), current, &ready); err != nil {
					t.Fatal(err)
				}
			},
		},
		{
			name: "fail", wantSuffix: "|ft_browser|0.000",
			settle: func(t *testing.T, q *b0Queue, current *lease) {
				t.Helper()
				if err := q.fail(context.Background(), current, 5000); err != nil {
					t.Fatal(err)
				}
			},
		},
		{
			name: "reap", wantSuffix: "|ft_browser|0.000",
			settle: func(t *testing.T, q *b0Queue, _ *lease) {
				t.Helper()
				time.Sleep(5 * time.Millisecond)
				if err := q.reap(context.Background(), 1); err != nil {
					t.Fatal(err)
				}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, queue, owner := integrationRedisQueue(t)
			task := integrationTask(t, "pending-"+test.name, 1, 0)
			integrationActivate(t, queue, owner, task, 0, false)
			current, _, err := queue.claim(context.Background(), func() time.Duration {
				if test.name == "reap" {
					return time.Millisecond
				}
				return time.Minute
			}())
			if err != nil || current == nil {
				t.Fatalf("claim failed: %#v %v", current, err)
			}
			integrationActivate(t, queue, owner, task, 0, true)
			pending := integrationRecord(t, client, queue, task.Envelope.TaskID)
			if pending["pending_ready_at_ms"] != float64(0) || pending["pending_first_time"] != true {
				t.Fatalf("inflight enqueue was not recorded exactly: %#v", pending)
			}
			test.settle(t, queue, current)
			settled := integrationRecord(t, client, queue, task.Envelope.TaskID)
			if settled["state"] != "ready" || settled["ready_at_ms"] != float64(0) ||
				settled["pending_ready_at_ms"] != nil || settled["pending_first_time"] != nil {
				t.Fatalf("pending enqueue was lost at %s: %#v", test.name, settled)
			}
			guard, err := client.HGet(context.Background(), legacyGuardKey, task.Envelope.TaskID).Result()
			if err != nil || !strings.HasSuffix(guard, test.wantSuffix) {
				t.Fatalf("rollback guard lost %s intent: %q %v", test.name, guard, err)
			}
			if err := queue.audit(context.Background()); err != nil {
				t.Fatalf("%s broke conservation: %v", test.name, err)
			}
		})
	}
}

func integrationRollback(
	t *testing.T,
	queue *b0Queue,
	owner producerOwnerIdentity,
	task queueTask,
	firstTime bool,
	score string,
) time.Duration {
	t.Helper()
	plan := map[string]any{
		task.Envelope.TaskID: map[string]any{
			"action": "schedule", "domain": task.Envelope.Domain, "worker_type": "browser",
			"first_time": firstTime, "score": score,
			"config": map[string]any{
				"domain": task.Envelope.Domain, "board_id": task.Envelope.BoardID,
				"source_url": task.Envelope.SourceURL, "description_r2_hash": "",
				"scrape_step": "0", "scrape_interval_hours": "24",
			},
		},
	}
	duration, scheduled, dropped := integrationRollbackPlan(t, queue, owner, plan)
	if scheduled != 1 || dropped != 0 {
		t.Fatalf("unexpected rollback counts: scheduled=%d dropped=%d", scheduled, dropped)
	}
	prefix := "scrapes_browser:"
	if firstTime {
		prefix = "ft_scrapes_browser:"
	}
	rolledBackScore, err := queue.client.ZScore(context.Background(), prefix+task.Envelope.Domain, task.Envelope.TaskID).Result()
	wantScore, _ := time.ParseDuration(score + "s")
	if err != nil || rolledBackScore != wantScore.Seconds() {
		t.Fatalf("rollback lost exact schedule: score=%v err=%v", rolledBackScore, err)
	}
	return duration
}

func integrationRollbackPlan(
	t *testing.T,
	queue *b0Queue,
	owner producerOwnerIdentity,
	plan map[string]any,
) (time.Duration, int64, int64) {
	t.Helper()
	encoded, err := canonicalJSON(plan, true)
	if err != nil {
		t.Fatal(err)
	}
	argv := []any{
		"rollback_legacy", owner.Route.ShardID, "7", owner.Route.EngineOwner, "", "0", "", "0", "0", "0",
		"", "", "", "64", "0", strings.Repeat("a", 64), "0", owner.Namespace, string(encoded), "1",
		owner.Cohort, "1", strings.Repeat("b", 64), owner.BoardSlugs[0],
	}
	started := time.Now()
	raw, err := queue.script.Run(context.Background(), queue.client, queue.keys, argv...).Result()
	duration := time.Since(started)
	if err != nil {
		t.Fatal(err)
	}
	fields, ok := raw.([]any)
	if !ok || len(fields) != 12 || fmt.Sprint(fields[0]) != "accepted" || fmt.Sprint(fields[1]) != "rolled_back" {
		t.Fatalf("rollback failed: %#v", raw)
	}
	scheduled, err := strconv.ParseInt(fmt.Sprint(fields[10]), 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	dropped, err := strconv.ParseInt(fmt.Sprint(fields[11]), 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	return duration, scheduled, dropped
}

func TestRealRedisTerminalAndDeadReactivationReplaceRollbackIntent(t *testing.T) {
	for _, test := range []struct {
		name, terminalState, score, suffix string
		firstTime                          bool
	}{
		{name: "terminal-to-recurring", terminalState: "terminal", score: "0.500", suffix: "|recurring_browser|0.500"},
		{name: "dead-to-first-time", terminalState: "dead", score: "0.000", suffix: "|ft_browser|0.000", firstTime: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			client, queue, owner := integrationRedisQueue(t)
			base := integrationTask(t, "reactivate-"+test.terminalState, 1, 0)
			integrationActivate(t, queue, owner, base, 0, false)
			leaseTTL := time.Minute
			if test.terminalState == "dead" {
				leaseTTL = time.Millisecond
			}
			lease, _, err := queue.claim(context.Background(), leaseTTL)
			if err != nil || lease == nil {
				t.Fatalf("claim failed: %#v %v", lease, err)
			}
			if test.terminalState == "terminal" {
				if err := queue.terminal(context.Background(), lease, nil); err != nil {
					t.Fatal(err)
				}
			} else {
				time.Sleep(5 * time.Millisecond)
				if err := queue.reap(context.Background(), 1); err != nil {
					t.Fatal(err)
				}
			}
			readyAt := int64(500)
			if test.firstTime {
				readyAt = 0
			}
			reactivated := integrationTask(t, base.Envelope.TaskID, 2, readyAt)
			result, err := queue.activateLegacy(
				context.Background(), &reactivated, readyAt, integrationLegacyConfig(reactivated),
				base.PayloadSHA256, false, test.firstTime, owner,
			)
			if err != nil || result.Reason != "reactivated" {
				t.Fatalf("reactivation failed: %#v %v", result, err)
			}
			guard, err := client.HGet(context.Background(), legacyGuardKey, base.Envelope.TaskID).Result()
			if err != nil || !strings.HasSuffix(guard, test.suffix) {
				t.Fatalf("reactivation retained stale guard: %q %v", guard, err)
			}
			if duration := integrationRollback(t, queue, owner, reactivated, test.firstTime, test.score); duration >= 5*time.Second {
				t.Fatalf("single-record rollback exceeded 5s: %s", duration)
			}
		})
	}
}

func TestRealRedisFullAuditAndCapacityBounds(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pipeline := client.Pipeline()
	var firstTask queueTask
	for index := int64(0); index < queueRecordLimit; index++ {
		taskID := fmt.Sprintf("capacity-%04d", index)
		task := integrationTask(t, taskID, 1, 0)
		if index == 0 {
			firstTask = task
		}
		if index == queuePilotOccupancyLimit-1 {
			if _, err := pipeline.Exec(ctx); err != nil {
				t.Fatal(err)
			}
			started := time.Now()
			result, err := queue.activateLegacy(
				ctx, &task, 0, integrationLegacyConfig(task), "", false, false, owner,
			)
			enqueueDuration := time.Since(started)
			if err != nil || result.Reason != "activated" || enqueueDuration >= 3*time.Second {
				t.Fatalf("near-threshold enqueue bounds: result=%#v duration=%s err=%v", result, enqueueDuration, err)
			}
			pilotTask := integrationTask(t, "pilot-new", 1, 0)
			_, err = queue.activateLegacy(ctx, &pilotTask, 0, integrationLegacyConfig(pilotTask), "", false, false, owner)
			var pilotCapacity queueCapacityError
			if !errors.As(err, &pilotCapacity) || pilotCapacity.Reason != "pilot_occupancy_limit" ||
				pilotCapacity.Occupancy != queuePilotOccupancyLimit || pilotCapacity.Capacity != queueRecordLimit {
				t.Fatalf("pilot occupancy limit was not typed: %#v %v", pilotCapacity, err)
			}
			reactivated := integrationTask(t, firstTask.Envelope.TaskID, 2, 500)
			result, err = queue.activateLegacy(
				ctx, &reactivated, 500, integrationLegacyConfig(reactivated), firstTask.PayloadSHA256,
				false, false, owner,
			)
			if err != nil || result.Reason != "reactivated" {
				t.Fatalf("existing ID was blocked by pilot occupancy limit: %#v %v", result, err)
			}
			t.Logf("1599-record successful enqueue: %s", enqueueDuration)
			pipeline = client.Pipeline()
			continue
		}
		record := map[string]any{
			"task_id": taskID, "task_kind": "scrape", "state": "terminal",
			"shard_id": owner.Route.ShardID, "routing_epoch": owner.Route.RoutingEpoch,
			"engine_owner": owner.Route.EngineOwner, "config_revision": int64(1),
			"policy_key": task.Envelope.PolicyKey, "domain": task.Envelope.Domain,
			"payload": task.Payload, "payload_sha256": task.PayloadSHA256, "payload_sha1": task.PayloadSHA1,
			"claim_token": nil, "claim_sequence": nil, "lease_until_ms": nil,
			"ready_at_ms": nil, "visible_at_ms": nil, "failures": int64(0),
			"pending_ready_at_ms": nil, "pending_first_time": nil,
		}
		encoded, err := canonicalJSON(record, true)
		if err != nil {
			t.Fatal(err)
		}
		pipeline.HSet(ctx, queue.keys[1], taskID, string(encoded))
		pipeline.SAdd(ctx, queue.keys[5], taskID)
		pipeline.HSet(ctx, legacyGuardKey, taskID,
			fmt.Sprintf("real-redis|lightpanda-b0|7|board-1|jobs.example.com|recurring_browser|0.000"))
	}
	if _, err := pipeline.Exec(ctx); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	if err := queue.audit(ctx); err != nil {
		t.Fatal(err)
	}
	duration := time.Since(started)
	if duration >= 2*time.Second {
		t.Fatalf("worst-case 2048-record audit exceeded 2s: %s", duration)
	}
	newTask := integrationTask(t, "capacity-new", 1, 0)
	_, err := queue.activateLegacy(ctx, &newTask, 0, integrationLegacyConfig(newTask), "", false, false, owner)
	var capacity queueCapacityError
	if !errors.As(err, &capacity) || capacity.Reason != "namespace_full" || capacity.Occupancy != 2048 || capacity.Capacity != 2048 {
		t.Fatalf("physical capacity was not typed: %#v %v", capacity, err)
	}
	if occupancy, err := queue.lifetimeOccupancy(ctx); err != nil || occupancy != 2048 {
		t.Fatalf("lifetime occupancy was not exact: %d %v", occupancy, err)
	}
	plan := make(map[string]any, queueRecordLimit)
	for index := int64(0); index < queueRecordLimit; index++ {
		taskID := fmt.Sprintf("capacity-%04d", index)
		score := "0.000"
		if index == 0 {
			score = "0.500"
		}
		plan[taskID] = map[string]any{
			"action": "schedule", "domain": firstTask.Envelope.Domain, "worker_type": "browser",
			"first_time": false, "score": score,
			"config": map[string]any{
				"domain": firstTask.Envelope.Domain, "board_id": firstTask.Envelope.BoardID,
				"source_url": firstTask.Envelope.SourceURL, "description_r2_hash": "",
				"scrape_step": "0", "scrape_interval_hours": "24",
			},
		}
	}
	rollbackDuration, scheduled, dropped := integrationRollbackPlan(t, queue, owner, plan)
	if rollbackDuration >= 5*time.Second || scheduled != queueRecordLimit || dropped != 0 {
		t.Fatalf("full rollback bounds/counts: duration=%s scheduled=%d dropped=%d", rollbackDuration, scheduled, dropped)
	}
	t.Logf("2048-record full audit: %s; full rollback: %s", duration, rollbackDuration)
}

func TestRealRedis1270ProducerTransfersWithinActivationDeadline(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	producer.queue, producer.route, producer.owner = queue, owner.Route, owner
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	request := validProducerRequest()
	pipeline := client.Pipeline()
	for index := 0; index < 1270; index++ {
		taskID := fmt.Sprintf("producer-scale-%04d", index)
		pipeline.HSet(ctx, "scrape:"+taskID,
			"board_id", request.Config["board_id"],
			"source_url", request.Config["source_url"],
			"domain", request.Domain,
			"scrape_step", "0",
			"scrape_interval_hours", "24",
			"description_r2_hash", "",
		)
		pipeline.ZAdd(ctx, "scrapes_browser:"+request.Domain, redis.Z{Score: 123, Member: taskID})
	}
	pipeline.ZAdd(ctx, "ready:browser:2", redis.Z{Score: 123, Member: request.Domain})
	if _, err := pipeline.Exec(ctx); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	for index := 0; index < 1270; index++ {
		request.PostingID = fmt.Sprintf("producer-scale-%04d", index)
		request.Operation, request.OperatorTransfer, request.ExpectedDigest = "prepare", true, ""
		prepared, err := producer.prepare(ctx, request)
		if err != nil {
			t.Fatalf("prepare %d failed: %v", index, err)
		}
		request.Operation, request.ExpectedDigest = "activate", prepared.digest
		_, result, err := producer.activate(ctx, request)
		if err != nil || result.Reason != "activated" {
			t.Fatalf("activate %d failed: %#v %v", index, result, err)
		}
	}
	duration := time.Since(started)
	if duration >= 90*time.Second {
		t.Fatalf("1270-task producer transfer exceeded activation deadline: %s", duration)
	}
	if occupancy, err := queue.lifetimeOccupancy(ctx); err != nil || occupancy != 1270 {
		t.Fatalf("producer transfer lost records: occupancy=%d err=%v", occupancy, err)
	}
	if err := queue.audit(ctx); err != nil {
		t.Fatalf("producer transfer broke conservation: %v", err)
	}
	t.Logf("1270-task sequential producer transfer: %s", duration)
}

const persistenceTaskID = "persistence-restart-proof"

func persistenceRedisQueue(t *testing.T) (*redis.Client, *b0Queue, producerOwnerIdentity) {
	t.Helper()
	rawURL := os.Getenv("LIGHTPANDA_B0_INTEGRATION_REDIS_URL")
	if rawURL == "" {
		t.Skip("LIGHTPANDA_B0_INTEGRATION_REDIS_URL is not configured")
	}
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	t.Cleanup(func() { _ = client.Close() })
	route := routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner}
	queue, err := newB0Queue(client, "../../src/lua/lightpanda_b0_queue.lua", "real-redis-persistence", route, "0", newMetrics())
	if err != nil {
		t.Fatal(err)
	}
	owner := producerOwnerIdentity{
		Namespace: queue.namespace, Cohort: "c1", Route: route,
		BoardSlugs: []string{"browser-use-careers"},
	}
	return client, queue, owner
}

// TestRealRedisPersistenceSeed and TestRealRedisPersistenceVerify are separate
// entry points so CI can SIGKILL and recreate Redis between them on one volume.
func TestRealRedisPersistenceSeed(t *testing.T) {
	client, queue, owner := persistenceRedisQueue(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	task := integrationTask(t, persistenceTaskID, 1, 123000)
	legacyConfig := map[string]any{
		"domain": task.Envelope.Domain, "board_id": task.Envelope.BoardID,
		"source_url": task.Envelope.SourceURL, "description_r2_hash": "",
		"scrape_step": "0", "scrape_interval_hours": "24",
	}
	if err := client.HSet(ctx, "scrape:"+persistenceTaskID, legacyConfig).Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.ZAdd(ctx, "scrapes_browser:"+task.Envelope.Domain, redis.Z{Score: 123, Member: persistenceTaskID}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.ZAdd(ctx, "ready:browser:2", redis.Z{Score: 123, Member: task.Envelope.Domain}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := queue.initializeProducer(ctx, owner); err != nil {
		t.Fatal(err)
	}
	if err := queue.persistProducer(ctx); err != nil {
		t.Fatalf("owner persistence failed: %v", err)
	}
	result, err := queue.activateLegacy(
		ctx, &task, 123000, integrationLegacyConfig(task), "", true, false, owner,
	)
	if err != nil || result.Reason != "activated" || result.SecondaryValue != 1 {
		t.Fatalf("persistent activation failed: %#v %v", result, err)
	}
	if err := queue.persistProducer(ctx); err != nil {
		t.Fatalf("record persistence failed: %v", err)
	}
}

func TestRealRedisPersistenceVerify(t *testing.T) {
	client, queue, owner := persistenceRedisQueue(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	bootstrap, err := queue.preflight(ctx, true, owner)
	if err != nil || bootstrap {
		t.Fatalf("persisted route/owner proof failed: bootstrap=%t err=%v", bootstrap, err)
	}
	stored, err := queue.inspect(ctx, persistenceTaskID)
	if err != nil || stored == nil || stored.State != "ready" || stored.Task.Envelope.ConfigRevision != 1 ||
		stored.Task.Envelope.InitialReadyAtMS != 123000 {
		t.Fatalf("persisted record proof failed: %#v %v", stored, err)
	}
	for _, key := range []string{
		"ft_scrapes_browser:" + stored.Task.Envelope.Domain,
		"scrapes_browser:" + stored.Task.Envelope.Domain,
	} {
		if _, err := client.ZScore(ctx, key, persistenceTaskID).Result(); !errors.Is(err, redis.Nil) {
			t.Fatalf("legacy membership survived activation in %s: %v", key, err)
		}
	}
	if err := queue.audit(ctx); err != nil {
		t.Fatalf("persisted queue conservation failed: %v", err)
	}
}
