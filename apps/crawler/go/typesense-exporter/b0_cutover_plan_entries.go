package main

import (
	"strconv"
	"strings"
	"time"
)

// Populated from typed authoritative PG rows and the audited queue snapshot.
// Domain is resolved from the current source URL before this assembly step.
type b0PlanPosting struct {
	ID, BoardID, SourceURL, Domain, BoardStatus string
	Enabled, Active, Browser, LeaseActive       bool
	NextScrapeAt                                *time.Time
	Hash                                        *int64
	Interval                                    int64
}

type b0PlanStored struct{ BoardID, SourceURL, Domain, State, LegacyGuard string }

func b0PlanConfig(row b0PlanPosting) (map[string]string, error) {
	if row.ID == "" || row.BoardID == "" || row.SourceURL == "" || row.Domain == "" || row.Interval < 1 || row.Interval > 8760 {
		return nil, errB0Cutover
	}
	hash := ""
	if row.Hash != nil {
		hash = strconv.FormatInt(*row.Hash, 10)
	}
	return map[string]string{"domain": row.Domain, "board_id": row.BoardID, "source_url": row.SourceURL, "description_r2_hash": hash, "scrape_step": "0", "scrape_interval_hours": strconv.FormatInt(row.Interval, 10)}, nil
}

func b0PlanRollbackEntry(taskID string, stored b0PlanStored, row *b0PlanPosting, identity b0CutoverIdentity) (map[string]any, map[string]any, error) {
	if taskID == "" || stored.State != "ready" && stored.State != "dead" && stored.State != "terminal" {
		return nil, nil, errB0Cutover
	}
	if row != nil && row.LeaseActive {
		return nil, nil, errB0Cutover
	}
	if row == nil || !row.Active || row.NextScrapeAt == nil || !row.Enabled || row.BoardStatus != "active" {
		return map[string]any{"posting_id": taskID, "action": "drop"}, map[string]any{"action": "drop"}, nil
	}
	if row.ID != taskID || row.LeaseActive {
		return nil, nil, errB0Cutover
	}
	config, err := b0PlanConfig(*row)
	if err != nil {
		return nil, nil, err
	}
	firstTime := row.Hash == nil
	worker := "simple"
	if row.Browser {
		worker = "browser"
	}
	score := "0"
	if stored.State == "ready" {
		kind, guardScore, err := b0PlanBoundGuard(stored.LegacyGuard, identity, stored.BoardID, stored.Domain)
		if err != nil {
			return nil, nil, err
		}
		firstTime = strings.HasPrefix(kind, "ft_")
		worker = strings.TrimPrefix(strings.TrimPrefix(kind, "ft_"), "recurring_")
		score = guardScore
	} else if !firstTime {
		score, err = b0PlanSeconds(*row.NextScrapeAt)
		if err != nil {
			return nil, nil, err
		}
	}
	entry := map[string]any{"action": "schedule", "domain": row.Domain, "worker_type": worker, "first_time": firstTime, "score": score, "config": config}
	task := make(map[string]any, len(entry)+2)
	for k, v := range entry {
		task[k] = v
	}
	task["posting_id"] = taskID
	if stored.BoardID != row.BoardID || stored.SourceURL != row.SourceURL {
		task["identity_changed_since_activation"] = true
	}
	return task, entry, nil
}
