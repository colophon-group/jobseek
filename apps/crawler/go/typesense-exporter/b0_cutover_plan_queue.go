package main

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

type b0PlanRecord struct {
	Task     b0task.Task
	State    string
	Failures int64
}

func decodeB0PlanRecord(taskID, raw string, identity b0CutoverIdentity) (b0PlanRecord, error) {
	var record b0PlanRecord
	var fields map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &fields) != nil || len(fields) != 20 {
		return record, errB0Cutover
	}
	for _, name := range []string{"task_id", "task_kind", "state", "shard_id", "routing_epoch", "engine_owner", "config_revision", "policy_key", "domain", "payload", "payload_sha256", "payload_sha1", "claim_token", "claim_sequence", "lease_until_ms", "ready_at_ms", "visible_at_ms", "failures", "pending_ready_at_ms", "pending_first_time"} {
		if _, ok := fields[name]; !ok {
			return record, errB0Cutover
		}
	}
	text := func(name string) (string, error) {
		var value string
		if string(fields[name]) == "null" || json.Unmarshal(fields[name], &value) != nil {
			return "", errB0Cutover
		}
		return value, nil
	}
	integers := func(name string) (int64, error) {
		var value int64
		if string(fields[name]) == "null" || json.Unmarshal(fields[name], &value) != nil {
			return 0, errB0Cutover
		}
		return value, nil
	}
	for name, want := range map[string]string{"task_id": taskID, "shard_id": identity.Shard, "engine_owner": "go"} {
		got, err := text(name)
		if err != nil || got != want {
			return record, errB0Cutover
		}
	}
	epoch, err := integers("routing_epoch")
	if err != nil || epoch != identity.Epoch {
		return record, errB0Cutover
	}
	state, err := text("state")
	if err != nil || !(state == "ready" || state == "inflight" || state == "dead" || state == "terminal") {
		return record, errB0Cutover
	}
	failures, err := integers("failures")
	if err != nil || failures < 0 || failures > 100 {
		return record, errB0Cutover
	}
	readyNull := string(fields["pending_ready_at_ms"]) == "null"
	firstNull := string(fields["pending_first_time"]) == "null"
	if readyNull != firstNull {
		return record, errB0Cutover
	}
	if !readyNull {
		pending, err := integers("pending_ready_at_ms")
		var first bool
		if state != "inflight" || err != nil || pending < 0 || pending > b0MaxEpoch || json.Unmarshal(fields["pending_first_time"], &first) != nil {
			return record, errB0Cutover
		}
	}
	payload, err := text("payload")
	if err != nil {
		return record, errB0Cutover
	}
	digest, err := text("payload_sha256")
	if err != nil {
		return record, errB0Cutover
	}
	task, err := b0task.Decode(payload, digest, b0task.Route{ShardID: identity.Shard, RoutingEpoch: identity.Epoch, EngineOwner: "go"})
	if err != nil {
		return record, errB0Cutover
	}
	legacy, err := text("payload_sha1")
	if err != nil || legacy != task.PayloadSHA1 || task.Envelope.TaskID != taskID {
		return record, errB0Cutover
	}
	revision, err := integers("config_revision")
	if err != nil || revision != task.Envelope.ConfigRevision {
		return record, errB0Cutover
	}
	policy, err := text("policy_key")
	if err != nil || policy != task.Envelope.PolicyKey {
		return record, errB0Cutover
	}
	return b0PlanRecord{Task: task, State: state, Failures: failures}, nil
}

func (q *b0ColdQueue) planRecords(ctx context.Context, allowDead bool) (map[string]b0PlanRecord, error) {
	values := map[string]b0PlanRecord{}
	types, err := q.namespaceTypes(ctx)
	if err != nil {
		return nil, err
	}
	if types[0] == "none" {
		for _, kind := range types[1:] {
			if kind != "none" {
				return nil, errB0Cutover
			}
		}
		return values, nil
	}
	audit, err := q.call(ctx, "audit", false)
	if err != nil {
		return nil, err
	}
	raw, err := q.client.HGetAll(ctx, q.keys[1]).Result()
	if err != nil || int64(len(raw)) != audit.Value {
		return nil, errB0Cutover
	}
	for id, body := range raw {
		value, err := decodeB0PlanRecord(id, body, q.identity)
		if err != nil || value.State == "inflight" || !allowDead && value.State == "dead" {
			return nil, errB0Cutover
		}
		values[id] = value
	}
	return values, nil
}

func (q *b0ColdQueue) planNoSuffixAuthority(ctx context.Context, ids map[string]bool) error {
	if len(ids) == 0 {
		return nil
	}
	for _, worker := range []string{"simple", "browser"} {
		for _, prefix := range []string{"inflight", "deadletter"} {
			iterator := q.client.ZScan(ctx, prefix+":"+worker, 0, "", 0).Iterator()
			for iterator.Next(ctx) {
				member := iterator.Val()
				if !iterator.Next(ctx) {
					return errB0Cutover
				}
				if strings.HasPrefix(member, "scrape|") {
					index := strings.LastIndexByte(member, '|')
					if index >= 0 && ids[member[index+1:]] {
						return errB0Cutover
					}
				}
			}
			if iterator.Err() != nil {
				return errB0Cutover
			}
		}
	}
	return nil
}
