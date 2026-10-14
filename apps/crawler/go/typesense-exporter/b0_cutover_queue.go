package main

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/redis/go-redis/v9"
)

//go:embed b0_cutover_queue.lua
var b0CutoverQueueLua string

const b0CutoverQueueSHA = "81327c30281b99f1e3495bed6be8b79cb0a01f7af70987bde6ffe6cedd19557f"
const b0OwnerKey = "lightpanda-b0:producer-owner"
const b0GuardKey = "lightpanda-b0:legacy-guard"
const b0OwnerSchema = "jobseek.lightpanda.producer-owner/v1"
const b0RollbackSchema = "jobseek.lightpanda.producer-rollback/v1"

var b0Namespace = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`)
var b0RouteID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)

type b0ColdQueue struct {
	client   *redis.Client
	identity b0CutoverIdentity
	keys     []string
	script   *redis.Script
}

type b0ColdTransition struct{ ServerMS, Value, Secondary int64 }

func newB0ColdQueue(client *redis.Client, identity b0CutoverIdentity) (*b0ColdQueue, error) {
	digest := sha256.Sum256([]byte(b0CutoverQueueLua))
	if client == nil || !b0Namespace.MatchString(identity.Namespace) || !b0RouteID.MatchString(identity.Shard) || identity.Epoch < 1 || identity.Epoch > b0MaxEpoch || !capacityDigest.MatchString(identity.SourceReceipt) || !(identity.Cohort == "c1" || identity.Cohort == "c2" || identity.Cohort == "c3" || identity.Cohort == "cdom") || hex.EncodeToString(digest[:]) != b0CutoverQueueSHA {
		return nil, errB0Cutover
	}
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{"+identity.Namespace+"}:"+suffix)
	}
	return &b0ColdQueue{client: client, identity: identity, keys: keys, script: redis.NewScript(b0CutoverQueueLua)}, nil
}

func b0ColdUint(raw string) (int64, error) {
	if raw == "" || strings.Trim(raw, "0123456789") != "" {
		return 0, errB0Cutover
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || n < 0 || n > b0MaxEpoch {
		return 0, errB0Cutover
	}
	return n, nil
}

func decodeB0ColdTransition(operation string, raw any) (b0ColdTransition, error) {
	result := b0ColdTransition{}
	values, ok := raw.([]any)
	if !ok || len(values) != 12 {
		return result, errB0Cutover
	}
	fields := make([]string, 12)
	for i, value := range values {
		text, ok := value.(string)
		if !ok || !utf8.ValidString(text) {
			return result, errB0Cutover
		}
		fields[i] = text
	}
	if fields[0] != "accepted" {
		return result, errB0Cutover
	}
	valid := operation == "audit" && fields[1] == "audit_ok" || operation == "reap_expired" && fields[1] == "reaped" || operation == "clear_rollback_tombstone" && (fields[1] == "rollback_tombstone_cleared" || fields[1] == "rollback_tombstone_already_cleared")
	if !valid {
		return result, errB0Cutover
	}
	var err error
	result.ServerMS, err = b0ColdUint(fields[2])
	if err != nil || result.ServerMS < 1 {
		return result, errB0Cutover
	}
	for _, field := range fields[3:10] {
		if field != "" {
			return result, errB0Cutover
		}
	}
	if operation == "clear_rollback_tombstone" {
		if fields[10] != "" || fields[11] != "" {
			return result, errB0Cutover
		}
		return result, nil
	}
	result.Value, err = b0ColdUint(fields[10])
	if err != nil {
		return result, errB0Cutover
	}
	result.Secondary, err = b0ColdUint(fields[11])
	if err != nil {
		return result, errB0Cutover
	}
	if operation == "audit" && (result.Value > 2048 || result.Secondary > result.Value) || operation == "reap_expired" && result.Value+result.Secondary > 64 {
		return result, errB0Cutover
	}
	return result, nil
}

func (q *b0ColdQueue) call(ctx context.Context, operation string, allowAbsent bool) (b0ColdTransition, error) {
	if operation != "audit" && operation != "reap_expired" && operation != "clear_rollback_tombstone" {
		return b0ColdTransition{}, errB0Cutover
	}
	identity := q.identity
	failures, operator, cohort, digest, receipt := "0", "0", "", "", "0"
	if operation == "reap_expired" {
		failures = "3"
	}
	if operation == "clear_rollback_tombstone" {
		if !capacityDigest.MatchString(identity.PlanDigest) {
			return b0ColdTransition{}, errB0Cutover
		}
		cohort, digest, receipt = identity.Cohort, identity.PlanDigest, identity.SourceReceipt
		if allowAbsent {
			operator = "1"
		}
	}
	args := []any{operation, identity.Shard, strconv.FormatInt(identity.Epoch, 10), "go", "", "0", "", "0", "0", failures, "", "", "", "64", "0", digest, "0", identity.Namespace, "", operator, cohort, "0", receipt}
	raw, err := q.script.Run(ctx, q.client, q.keys, args...).Result()
	if err != nil {
		return b0ColdTransition{}, errB0Cutover
	}
	return decodeB0ColdTransition(operation, raw)
}

func (q *b0ColdQueue) namespaceTypes(ctx context.Context) ([]string, error) {
	values := make([]string, 0, len(q.keys))
	for _, key := range q.keys {
		value, err := q.client.Type(ctx, key).Result()
		if err != nil {
			return nil, errB0Cutover
		}
		values = append(values, value)
	}
	return values, nil
}

func (q *b0ColdQueue) ownerSchema(ctx context.Context) (string, error) {
	kind, err := q.client.Type(ctx, b0OwnerKey).Result()
	if err != nil {
		return "", errB0Cutover
	}
	if kind == "none" {
		return "", nil
	}
	if kind != "hash" {
		return "", errB0Cutover
	}
	schema, err := q.client.HGet(ctx, b0OwnerKey, "schema").Result()
	if err != nil || schema == "" {
		return "", errB0Cutover
	}
	return schema, nil
}

func (q *b0ColdQueue) boundTombstone(ctx context.Context, expectedDigest string) (bool, error) {
	schema, err := q.ownerSchema(ctx)
	if err != nil {
		return false, err
	}
	if schema == "" {
		return false, nil
	}
	if schema != b0RollbackSchema {
		return false, errB0Cutover
	}
	values, err := q.client.HGetAll(ctx, b0OwnerKey).Result()
	if err != nil || len(values) != 8 {
		return false, errB0Cutover
	}
	i := q.identity
	expected := map[string]string{"schema": b0RollbackSchema, "namespace": i.Namespace, "shard_id": i.Shard, "routing_epoch": strconv.FormatInt(i.Epoch, 10), "engine_owner": "go", "cohort": i.Cohort, "source_receipt_sha256": i.SourceReceipt}
	for key, value := range expected {
		if values[key] != value {
			return false, errB0Cutover
		}
	}
	if !capacityDigest.MatchString(values["rollback_plan_digest"]) || expectedDigest != "" && values["rollback_plan_digest"] != expectedDigest {
		return false, errB0Cutover
	}
	types, err := q.namespaceTypes(ctx)
	if err != nil {
		return false, err
	}
	for _, kind := range types {
		if kind != "none" {
			return false, errB0Cutover
		}
	}
	guard, err := q.client.Type(ctx, b0GuardKey).Result()
	if err != nil || guard != "none" {
		return false, errB0Cutover
	}
	return true, nil
}
