package main

import (
	"context"
	"regexp"
	"strconv"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

var b0RollbackScore = regexp.MustCompile(`^(?:0|[1-9][0-9]*(?:\.[0-9]+)?)$`)
var b0RollbackDomain = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)

func validateB0RollbackEntries(entries map[string]any) error {
	if len(entries) > 2048 {
		return errB0Cutover
	}
	for id, raw := range entries {
		entry, ok := raw.(map[string]any)
		if !ok || !b0RouteID.MatchString(id) {
			return errB0Cutover
		}
		if entry["action"] == "drop" {
			if len(entry) != 1 {
				return errB0Cutover
			}
			continue
		}
		if entry["action"] != "schedule" || len(entry) != 6 {
			return errB0Cutover
		}
		domain, ok := entry["domain"].(string)
		if !ok || !b0RollbackDomain.MatchString(domain) {
			return errB0Cutover
		}
		worker, ok := entry["worker_type"].(string)
		if !ok || worker != "simple" && worker != "browser" {
			return errB0Cutover
		}
		if _, ok := entry["first_time"].(bool); !ok {
			return errB0Cutover
		}
		score, ok := entry["score"].(string)
		if !ok || !b0RollbackScore.MatchString(score) {
			return errB0Cutover
		}
		if _, err := b0PlanScore(score); err != nil {
			return errB0Cutover
		}
		config, ok := entry["config"].(map[string]string)
		if !ok || len(config) != 6 || config["domain"] != domain || config["scrape_step"] != "0" || !b0RouteID.MatchString(config["board_id"]) {
			return errB0Cutover
		}
		for _, name := range []string{"domain", "board_id", "source_url", "description_r2_hash", "scrape_step", "scrape_interval_hours"} {
			if _, ok := config[name]; !ok {
				return errB0Cutover
			}
		}
		if hash := config["description_r2_hash"]; hash != "" && !b0PlanCachedHash.MatchString(hash) {
			return errB0Cutover
		}
		interval, err := strconv.ParseInt(config["scrape_interval_hours"], 10, 64)
		if err != nil || interval < 1 || interval > 8760 {
			return errB0Cutover
		}
		actual, err := b0PlanDomain(config["source_url"])
		if err != nil || actual != domain {
			return errB0Cutover
		}
	}
	return nil
}

func (q *b0ColdQueue) rollbackPlan(ctx context.Context, entries map[string]any, digest string) (b0ColdTransition, error) {
	if validateB0RollbackEntries(entries) != nil {
		return b0ColdTransition{}, errB0Cutover
	}
	if !capacityDigest.MatchString(digest) || !capacityDigest.MatchString(q.identity.SourceReceipt) || len(entries) > 2048 {
		return b0ColdTransition{}, errB0Cutover
	}
	// Entries are assembled from typed current canonical rows, with exact guard
	// schedules. The unchanged Lua still preflights every entry before any effect.
	body, err := b0task.CanonicalJSON(entries, true)
	if err != nil || len(body) > 2*1024*1024 {
		return b0ColdTransition{}, errB0Cutover
	}
	i := q.identity
	args := []any{"rollback_legacy", i.Shard, strconv.FormatInt(i.Epoch, 10), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "0", digest, "0", i.Namespace, string(body), "0", i.Cohort, "0", i.SourceReceipt}
	raw, err := q.script.Run(ctx, q.client, q.keys, args...).Result()
	if err != nil {
		return b0ColdTransition{}, errB0Cutover
	}
	return decodeB0ColdTransition("rollback_legacy", raw)
}
