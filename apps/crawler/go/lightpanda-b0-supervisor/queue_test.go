package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func validQueueTask(t *testing.T) queueTask {
	t.Helper()
	parserConfig := json.RawMessage(`{"browser_backend":"lightpanda","render":true,"routing_revision":"b0+1","timeout":5000,"wait":"load","wait_fallback":null}`)
	assignmentDigest := sha256.Sum256(parserConfig)
	envelope := map[string]any{
		"assignment_digest_sha256": hex.EncodeToString(assignmentDigest[:]),
		"board_id":                 "board-1", "browser_backend": "lightpanda", "config_revision": int64(3),
		"domain": "jobs.example.com", "engine_owner": "go", "initial_ready_at_ms": int64(0),
		"parser_config": parserConfig, "policy_key": queuePolicyKey, "render": true,
		"routing_epoch": int64(7), "routing_revision": "b0+1", "schema_version": "lightpanda-b0-task-v1",
		"scraper_step": int64(0), "scraper_type": "json-ld", "shard_id": "lightpanda-b0",
		"source_url": "https://jobs.example.com/posting", "task_id": "00000000-0000-4000-8000-000000000001",
		"task_kind": "scrape", "timeout_ms": int64(5000), "wait": "load", "wait_fallback": nil,
	}
	payloadBytes, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(payloadBytes)
	task, err := decodeQueueTask(string(payloadBytes), hex.EncodeToString(digest[:]), routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"})
	if err != nil {
		t.Fatalf("valid task rejected: %v", err)
	}
	return task
}

func TestDecodeQueueTaskValidatesPayloadAndAssignmentIdentity(t *testing.T) {
	task := validQueueTask(t)
	if task.Envelope.TaskID != "00000000-0000-4000-8000-000000000001" || task.PayloadSHA1 == "" {
		t.Fatal("validated task lost its identity")
	}
	if _, err := decodeQueueTask(task.Payload+" ", task.PayloadSHA256, routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"}); err == nil {
		t.Fatal("payload digest mismatch was accepted")
	}
	trailing := task.Payload + `{}`
	digest := sha256.Sum256([]byte(trailing))
	if _, err := decodeQueueTask(trailing, hex.EncodeToString(digest[:]), task.EnvelopeRoute()); err == nil || !strings.Contains(err.Error(), "trailing") {
		t.Fatalf("trailing JSON was not rejected exactly: %v", err)
	}
	changed := strings.Replace(task.Payload, task.Envelope.AssignmentDigestSHA256, strings.Repeat("0", 64), 1)
	digest = sha256.Sum256([]byte(changed))
	if _, err := decodeQueueTask(changed, hex.EncodeToString(digest[:]), task.EnvelopeRoute()); err == nil || !strings.Contains(err.Error(), "assignment digest") {
		t.Fatalf("assignment digest mismatch was not rejected: %v", err)
	}
}

func (t queueTask) EnvelopeRoute() routeIdentity {
	return routeIdentity{ShardID: t.Envelope.ShardID, RoutingEpoch: t.Envelope.RoutingEpoch, EngineOwner: t.Envelope.EngineOwner}
}

func TestTransitionReplyRequiresExactFenceAndConservationShape(t *testing.T) {
	task := validQueueTask(t)
	fields := []string{"accepted", "claimed", "1000", task.Envelope.TaskID, "7:1", "61000", "3", task.PayloadSHA256, task.Payload, queuePolicyKey, "", ""}
	result := transition{Decision: "accepted", Reason: "claimed", ServerTimeMS: 1000, TaskID: fields[3], ClaimToken: "7:1", LeaseUntilMS: 61000, ConfigRevision: 3, PayloadSHA256: task.PayloadSHA256, Payload: task.Payload, PolicyKey: queuePolicyKey}
	if err := validateTransitionReply("claim_next", result, fields, task.EnvelopeRoute(), nil, "", time.Minute, 0, 0); err != nil {
		t.Fatalf("valid claim reply rejected: %v", err)
	}
	result.LeaseUntilMS++
	if err := validateTransitionReply("claim_next", result, fields, task.EnvelopeRoute(), nil, "", time.Minute, 0, 0); err == nil {
		t.Fatal("invalid lease arithmetic was accepted")
	}
	rejected := make([]string, 12)
	rejected[0], rejected[1], rejected[2], rejected[3] = "not_current", "no_work", "1000", "leaked"
	if err := validateTransitionReply("claim_next", transition{Decision: "not_current", Reason: "no_work", ServerTimeMS: 1000}, rejected, task.EnvelopeRoute(), nil, "", time.Minute, 0, 0); err == nil {
		t.Fatal("rejected transition field leak was accepted")
	}
	audit := []string{"accepted", "audit_ok", "1000", "", "", "", "", "", "", "", "513", "0"}
	if err := validateTransitionReply("audit", transition{Decision: "accepted", Reason: "audit_ok", ServerTimeMS: 1000, Value: 513}, audit, task.EnvelopeRoute(), nil, "", 0, 0, 0); err == nil {
		t.Fatal("unbounded audit reply was accepted")
	}
}

func TestReviewedLuaDigestIsPinned(t *testing.T) {
	luaPath := filepath.Join("..", "..", "src", "lua", "lightpanda_b0_queue.lua")
	client := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"})
	t.Cleanup(func() { _ = client.Close() })
	if _, err := newB0Queue(client, luaPath, "test-b0", routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"}, "2.0", newMetrics()); err != nil {
		t.Fatalf("reviewed Lua rejected: %v", err)
	}
	contents, err := os.ReadFile(luaPath)
	if err != nil {
		t.Fatal(err)
	}
	changed := filepath.Join(t.TempDir(), "queue.lua")
	if err := os.WriteFile(changed, append(contents, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := newB0Queue(client, changed, "test-b0", routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"}, "2.0", newMetrics()); err == nil || !strings.Contains(err.Error(), "digest") {
		t.Fatalf("changed Lua was not rejected: %v", err)
	}
}
