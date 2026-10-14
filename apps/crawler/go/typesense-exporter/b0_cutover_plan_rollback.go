package main

import (
	"context"
	"sort"
)

func b0PlanSortedSet(values map[string]bool) []string {
	result := []string{}
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func (a *b0CutoverAdmin) rollbackPlan(ctx context.Context, receiptState string) (b0CutoverPlan, error) {
	fail := func() (b0CutoverPlan, error) { return b0CutoverPlan{}, errB0Cutover }
	i := a.queue.identity
	if a.mode != "off" || !(receiptState == "active" || receiptState == "pending") || !capacityDigest.MatchString(i.SourceReceipt) {
		return fail()
	}
	types, err := a.queue.namespaceTypes(ctx)
	if err != nil {
		return fail()
	}
	present := types[0] != "none"
	phase, err := b0ProducerMarkerPhase(a.markerDirectory, a.markerUID, i)
	if err != nil {
		return fail()
	}
	schema, err := a.queue.ownerSchema(ctx)
	if err != nil {
		return fail()
	}
	doc := map[string]any{"schema": "jobseek.lightpanda-b0-cutover-plan/v1", "operation": "rollback", "cohort": i.Cohort, "receipt_state": receiptState, "source_receipt_sha256": i.SourceReceipt, "producer_marker_phase": phase, "namespace": i.Namespace, "shard_id": i.Shard, "routing_epoch": i.Epoch}
	if schema == b0RollbackSchema {
		if phase == "unsafe" {
			return fail()
		}
		bound, err := a.queue.boundTombstone(ctx, "")
		if err != nil || !bound {
			return fail()
		}
		digest, err := a.queue.client.HGet(ctx, b0OwnerKey, "rollback_plan_digest").Result()
		if err != nil {
			return fail()
		}
		fences, err := b0PlanReadFenceIDs(ctx, a.pool, i)
		if err != nil {
			return fail()
		}
		set := map[string]bool{}
		for _, id := range fences {
			if id == "" {
				return fail()
			}
			set[id] = true
		}
		tasks := []map[string]any{}
		doc["tasks"] = tasks
		doc["redis_plan"] = map[string]any{}
		doc["fence_task_ids"] = b0PlanSortedSet(set)
		doc["namespace_present"] = false
		doc["rollback_committed"] = true
		doc["rollback_plan_digest"] = digest
		return b0PlanFinish("rollback", i.Cohort, doc, tasks, digest)
	}
	if schema != "" && schema != b0OwnerSchema || present != (schema == b0OwnerSchema) {
		return fail()
	}
	records, err := a.queue.planRecords(ctx, true)
	if err != nil || !b0RollbackMarkerValid(receiptState, phase, present) {
		return fail()
	}
	if !present {
		kind, err := a.queue.client.Type(ctx, b0GuardKey).Result()
		if err != nil || kind != "none" {
			return fail()
		}
	}
	ids := map[string]bool{}
	for id := range records {
		ids[id] = true
	}
	if a.queue.planNoSuffixAuthority(ctx, ids) != nil {
		return fail()
	}
	fences, err := b0PlanReadFenceIDs(ctx, a.pool, i)
	if err != nil {
		return fail()
	}
	all := map[string]bool{}
	for id := range ids {
		all[id] = true
	}
	for _, id := range fences {
		if id == "" {
			return fail()
		}
		all[id] = true
	}
	rows, err := b0PlanReadPostings(ctx, a.pool, b0PlanSortedSet(all), true)
	if err != nil {
		return fail()
	}
	byID := map[string]b0PlanPostgresPosting{}
	for _, row := range rows {
		if row.ID == "" || row.LeaseActive != nil && *row.LeaseActive {
			return fail()
		}
		byID[row.ID] = row
	}
	tasks := []map[string]any{}
	redisPlan := map[string]any{}
	for _, id := range b0PlanSortedSet(ids) {
		record := records[id]
		envelope := record.Task.Envelope
		stored := b0PlanStored{BoardID: envelope.BoardID, SourceURL: envelope.SourceURL, Domain: envelope.Domain, State: record.State}
		var posting *b0PlanPosting
		if row, ok := byID[id]; ok && row.Active != nil && *row.Active && row.Next != nil && row.Enabled != nil && *row.Enabled && row.Status == "active" {
			value, err := b0PlanPostingFromPG(row)
			if err != nil {
				return fail()
			}
			posting = &value
			if record.State == "ready" {
				guard, err := a.queue.client.HGet(ctx, b0GuardKey, id).Result()
				if err != nil {
					return fail()
				}
				stored.LegacyGuard = guard
			}
		}
		task, entry, err := b0PlanRollbackEntry(id, stored, posting, i)
		if err != nil {
			return fail()
		}
		tasks = append(tasks, task)
		redisPlan[id] = entry
	}
	doc["tasks"] = tasks
	doc["redis_plan"] = redisPlan
	doc["fence_task_ids"] = b0PlanSortedSet(all)
	doc["namespace_present"] = present
	doc["rollback_committed"] = false
	return b0PlanFinish("rollback", i.Cohort, doc, tasks, "")
}

func (a *b0CutoverAdmin) applyRollback(ctx context.Context, receiptState, expected string) (map[string]any, error) {
	plan, err := a.rollbackPlan(ctx, receiptState)
	if err != nil || plan.Digest != expected {
		return nil, errB0Cutover
	}
	restored, dropped := int64(0), int64(0)
	if plan.Document["rollback_committed"] != true {
		entries, ok := plan.Document["redis_plan"].(map[string]any)
		if !ok {
			return nil, errB0Cutover
		}
		result, err := a.queue.rollbackPlan(ctx, entries, plan.RollbackDigest)
		if err != nil {
			return nil, err
		}
		restored, dropped = result.Value, result.Secondary
	}
	bound, err := a.queue.boundTombstone(ctx, plan.RollbackDigest)
	if err != nil || !bound {
		return nil, errB0Cutover
	}
	i := a.queue.identity
	if _, err = a.pool.Exec(ctx, "DELETE FROM lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2", i.Shard, i.Epoch); err != nil {
		return nil, errB0Cutover
	}
	var remaining int64
	if a.pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2", i.Shard, i.Epoch).Scan(&remaining) != nil || remaining != 0 {
		return nil, errB0Cutover
	}
	return map[string]any{"ready_restored": restored, "terminal_dropped": dropped, "write_fences_remaining": remaining, "digest": plan.Digest, "rollback_plan_digest": plan.RollbackDigest}, nil
}
