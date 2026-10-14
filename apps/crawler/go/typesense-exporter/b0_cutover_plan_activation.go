package main

import (
	"context"
	"math"
	"regexp"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

var b0PlanCachedHash = regexp.MustCompile(`^-?(?:0|[1-9][0-9]*)$`)

type b0PlanActivationItem struct {
	Posting                   b0PlanPosting
	Config                    map[string]string
	FirstTime                 bool
	SourceScore, PostgresHash string
}

func (a *b0CutoverAdmin) legacyPreflight(ctx context.Context, items []*b0PlanActivationItem, records map[string]b0PlanRecord) (map[string]bool, error) {
	ids := map[string]bool{}
	for _, item := range items {
		ids[item.Posting.ID] = true
	}
	if a.queue.planNoSuffixAuthority(ctx, ids) != nil {
		return nil, errB0Cutover
	}
	selected := map[string]bool{}
	for _, item := range items {
		id := item.Posting.ID
		domain := item.Posting.Domain
		current, err := a.queue.client.HGetAll(ctx, "scrape:"+id).Result()
		if err != nil {
			return nil, errB0Cutover
		}
		kinds := []string{}
		scores := []float64{}
		for _, worker := range []string{"simple", "browser"} {
			for _, prefix := range []string{"ft_scrapes", "scrapes"} {
				score, err := a.queue.client.ZScore(ctx, prefix+"_"+worker+":"+domain, id).Result()
				if err == redis.Nil {
					continue
				}
				if err != nil || worker != "browser" {
					return nil, errB0Cutover
				}
				kinds = append(kinds, prefix)
				scores = append(scores, score)
			}
		}
		record, exists := records[id]
		if !exists {
			if len(current) == 0 && len(kinds) == 0 {
				continue
			}
			if len(current) != len(item.Config) && len(current) != len(item.Config)-1 {
				return nil, errB0Cutover
			}
			for key, value := range current {
				expected, ok := item.Config[key]
				if !ok || key != "description_r2_hash" && value != expected {
					return nil, errB0Cutover
				}
			}
			for key := range item.Config {
				if _, ok := current[key]; !ok && key != "scrape_interval_hours" {
					return nil, errB0Cutover
				}
			}
			hint := current["description_r2_hash"]
			if hint != "" {
				n, err := strconv.ParseInt(hint, 10, 64)
				if len(hint) > 20 || !b0PlanCachedHash.MatchString(hint) || err != nil || strconv.FormatInt(n, 10) != hint {
					return nil, errB0Cutover
				}
			}
			item.PostgresHash = item.Config["description_r2_hash"]
			item.Config["description_r2_hash"] = hint
		}
		expected := 1
		if exists {
			expected = 0
		}
		if len(kinds) != expected {
			return nil, errB0Cutover
		}
		if exists {
			guard, err := a.queue.client.HGet(ctx, b0GuardKey, id).Result()
			if err != nil {
				return nil, errB0Cutover
			}
			kind, _, err := b0PlanBoundGuard(guard, a.queue.identity, record.Task.Envelope.BoardID, record.Task.Envelope.Domain)
			if err != nil {
				return nil, errB0Cutover
			}
			item.FirstTime = strings.HasPrefix(kind, "ft_")
			if record.State == "terminal" || record.State == "dead" {
				item.FirstTime = item.Config["description_r2_hash"] == ""
			}
			item.PostgresHash = item.Config["description_r2_hash"]
		} else {
			item.FirstTime = kinds[0] == "ft_scrapes"
			score := scores[0]
			if math.IsNaN(score) || math.IsInf(score, 0) || score < 0 || score > 9999999999.999 {
				return nil, errB0Cutover
			}
			item.SourceScore = strconv.FormatFloat(score, 'f', -1, 64)
		}
		selected[id] = item.FirstTime
	}
	return selected, nil
}

func (a *b0CutoverAdmin) activationPlan(ctx context.Context) (b0CutoverPlan, error) {
	fail := func() (b0CutoverPlan, error) { return b0CutoverPlan{}, errB0Cutover }
	if a.mode != "enabled" {
		return fail()
	}
	i := a.queue.identity
	manifest, err := a.producer.cutoverManifest(ctx, i.Cohort)
	if err != nil {
		return fail()
	}
	boards, boardDocs, err := a.validatedBoards(ctx, manifest.BoardSlugs)
	if err != nil {
		return fail()
	}
	rows, err := b0PlanReadPostings(ctx, a.pool, manifest.BoardSlugs, false)
	if err != nil {
		return fail()
	}
	scheduled := []b0PlanPostgresPosting{}
	for _, row := range rows {
		if row.LeaseActive != nil && *row.LeaseActive {
			return fail()
		}
		if row.Active != nil && *row.Active && row.Next != nil {
			scheduled = append(scheduled, row)
		}
	}
	if len(scheduled) == 0 || len(scheduled) > 2048 {
		return fail()
	}
	records, err := a.queue.planRecords(ctx, false)
	if err != nil || manifest.LifetimeOccupancy != int64(len(records)) {
		return fail()
	}
	scheduledIDs := map[string]bool{}
	for _, row := range scheduled {
		if row.ID == "" {
			return fail()
		}
		scheduledIDs[row.ID] = true
	}
	all := map[string]bool{}
	for id := range scheduledIDs {
		all[id] = true
	}
	retained := map[string]bool{}
	for id, record := range records {
		all[id] = true
		if !scheduledIDs[id] {
			if record.State != "terminal" {
				return fail()
			}
			if _, ok := boards[record.Task.Envelope.BoardID]; !ok {
				return fail()
			}
			retained[id] = true
		}
	}
	if a.queue.planNoSuffixAuthority(ctx, all) != nil {
		return fail()
	}
	items := []*b0PlanActivationItem{}
	for _, row := range scheduled {
		if _, ok := boards[row.BoardID]; !ok {
			return fail()
		}
		value, err := b0PlanPostingFromPG(row)
		if err != nil {
			return fail()
		}
		config, err := b0PlanConfig(value)
		if err != nil {
			return fail()
		}
		items = append(items, &b0PlanActivationItem{Posting: value, Config: config})
	}
	selected, err := a.legacyPreflight(ctx, items, records)
	if err != nil {
		return fail()
	}
	unqueued := map[string]bool{}
	newCount := int64(0)
	for _, item := range items {
		id := item.Posting.ID
		if _, ok := selected[id]; !ok {
			unqueued[id] = true
		} else if _, exists := records[id]; !exists {
			newCount++
		}
	}
	projected := manifest.LifetimeOccupancy + newCount
	if len(selected) == 0 || newCount > manifest.LifetimeHeadroom || projected > 1600 {
		return fail()
	}
	tasks := []map[string]any{}
	for _, item := range items {
		id := item.Posting.ID
		if _, ok := selected[id]; !ok {
			continue
		}
		ready, err := b0PlanReadyAt(*item.Posting.NextScrapeAt, item.FirstTime, item.SourceScore)
		if err != nil {
			return fail()
		}
		score, err := strconv.ParseFloat(ready, 64)
		if err != nil {
			return fail()
		}
		prepared, err := a.producer.cutoverTask(ctx, "prepare", item.Posting.Domain, id, score, item.Config, item.FirstTime, item.SourceScore, "")
		if err != nil {
			return fail()
		}
		state, hash := "", ""
		var existingState, existingHash any
		if record, ok := records[id]; ok {
			state = record.State
			hash = record.Task.PayloadSHA256
			existingState = state
			existingHash = hash
		}
		if prepared.ExistingState != state || prepared.ExistingPayloadSHA256 != hash {
			return fail()
		}
		tasks = append(tasks, map[string]any{"posting_id": id, "board_id": item.Posting.BoardID, "source_url": item.Posting.SourceURL, "domain": item.Posting.Domain, "next_scrape_at": ready, "first_time": item.FirstTime, "payload_sha256": prepared.PayloadSHA256, "preparation_digest": prepared.PreparationDigest, "existing_state": existingState, "existing_payload_sha256": existingHash, "legacy_config": item.Config, "legacy_schedule_score": item.SourceScore, "postgres_description_r2_hash": item.PostgresHash})
	}
	doc := map[string]any{"schema": "jobseek.lightpanda-b0-cutover-plan/v1", "operation": "activate", "cohort": i.Cohort, "namespace": i.Namespace, "shard_id": i.Shard, "routing_epoch": i.Epoch, "boards": boardDocs, "tasks": tasks, "unqueued_posting_ids": b0PlanSortedSet(unqueued), "retained_terminal_ids": b0PlanSortedSet(retained), "lifetime_occupancy": manifest.LifetimeOccupancy, "lifetime_capacity": manifest.LifetimeCapacity, "lifetime_headroom": manifest.LifetimeHeadroom, "new_record_count": newCount, "projected_lifetime_occupancy": projected, "pilot_rollback_signal_threshold": int64(1600)}
	return b0PlanFinish("activate", i.Cohort, doc, tasks, "")
}

func (a *b0CutoverAdmin) applyActivation(ctx context.Context, expected string) (map[string]any, error) {
	plan, err := a.activationPlan(ctx)
	if err != nil || plan.Digest != expected {
		return nil, errB0Cutover
	}
	activated := int64(0)
	ids := map[string]bool{}
	for _, item := range plan.Tasks {
		id := item["posting_id"].(string)
		score, err := strconv.ParseFloat(item["next_scrape_at"].(string), 64)
		if err != nil {
			return nil, errB0Cutover
		}
		result, err := a.producer.cutoverTask(ctx, "activate", item["domain"].(string), id, score, item["legacy_config"].(map[string]string), item["first_time"].(bool), item["legacy_schedule_score"].(string), item["preparation_digest"].(string))
		if err != nil {
			return nil, errB0Cutover
		}
		if result.Activated {
			activated++
		}
		ids[id] = true
	}
	audit, err := a.queue.call(ctx, "audit", false)
	if err != nil || audit.Value < int64(len(plan.Tasks)) || audit.Secondary != 0 {
		return nil, errB0Cutover
	}
	for _, id := range plan.Document["retained_terminal_ids"].([]string) {
		ids[id] = true
	}
	if a.queue.planNoSuffixAuthority(ctx, ids) != nil {
		return nil, errB0Cutover
	}
	manifest, err := a.producer.cutoverManifest(ctx, plan.Cohort)
	if err != nil || manifest.LifetimeOccupancy != plan.Document["projected_lifetime_occupancy"] || manifest.LifetimeOccupancy > 1600 || manifest.LifetimeCapacity != plan.Document["lifetime_capacity"] || manifest.LifetimeHeadroom != manifest.LifetimeCapacity-manifest.LifetimeOccupancy {
		return nil, errB0Cutover
	}
	return map[string]any{"selected": len(plan.Tasks), "unqueued": len(plan.Document["unqueued_posting_ids"].([]string)), "activated": activated, "already_activated": int64(len(plan.Tasks)) - activated, "digest": plan.Digest, "lifetime_occupancy": manifest.LifetimeOccupancy, "lifetime_headroom": manifest.LifetimeHeadroom, "pilot_rollback_signal_threshold": int64(1600)}, nil
}
