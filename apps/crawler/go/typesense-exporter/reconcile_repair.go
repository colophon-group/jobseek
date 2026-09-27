package main

import (
	"context"
	"errors"
	"log/slog"
	"time"
)

type reconciliationSource interface {
	fence(context.Context, func() error) error
	snapshot(context.Context, int) (reconciliationSnapshot, error)
	documents(context.Context, []string) ([]map[string]any, reconciliationSnapshot, error)
	existing(context.Context, []string) (reconciliationIDs, error)
}

type reconciliationIndex interface {
	partition(context.Context, int) (reconciliationSnapshot, error)
	upsert(context.Context, []map[string]any) (reconciliationIDs, error)
	deleteIDs(context.Context, []string) error
	unbucketed(context.Context) ([]reconciliationUnbucketed, error)
}

func (c reconciliationHTTP) upsert(ctx context.Context, documents []map[string]any) (reconciliationIDs, error) {
	failed, err := importDocs(ctx, c.Client, c.BaseURL, c.Key, documents)
	result := reconciliationIDs{}
	for id := range failed {
		result[id] = struct{}{}
	}
	return result, err
}

type reconciliationResult struct {
	Partition                                                                     int
	LocalRows, LocalActive, RemoteRows, RemoteActive                              int
	Missing, StateMismatch, PayloadMismatch, RemoteOnlyActive, RemoteOnlyInactive int
	Detected, Repaired, Unresolved                                                int
	Duration                                                                      float64
}

// Re-read only the frozen candidate set under the same cursor fence as CDC.
// Verify remote writes and local stability before returning; unrelated source
// changes never expand the repair or masquerade as a failed candidate write.
func repairReconciliationCandidates(ctx context.Context, source reconciliationSource, index reconciliationIndex, candidates reconciliationIDs, partition int) (int, int, error) {
	if len(candidates) == 0 {
		return 0, 0, nil
	}
	unresolved := reconciliationIDs{}
	err := source.fence(ctx, func() error {
		pending := candidates
		for attempt := 0; attempt < 2; attempt++ {
			ids := orderedReconciliationIDs(pending)
			documents, expected, err := source.documents(ctx, ids)
			if err != nil {
				return err
			}
			absent := make([]string, 0)
			for _, id := range ids {
				if _, exists := expected[id]; !exists {
					absent = append(absent, id)
				}
			}
			rejected := reconciliationIDs{}
			for start := 0; start < len(documents); start += 500 {
				failed, err := index.upsert(ctx, documents[start:min(start+500, len(documents))])
				if err != nil {
					return err
				}
				for id := range failed {
					rejected[id] = struct{}{}
				}
			}
			for start := 0; start < len(absent); start += 500 {
				if err := index.deleteIDs(ctx, absent[start:min(start+500, len(absent))]); err != nil {
					return err
				}
			}
			verified, err := index.partition(ctx, partition)
			if err != nil {
				return err
			}
			verificationFailed := compareReconciliationSnapshots(expected, verified.subset(pending)).candidates()
			_, current, err := source.documents(ctx, ids)
			if err != nil {
				return err
			}
			changed := compareReconciliationSnapshots(expected, current).candidates()
			for id := range rejected {
				verificationFailed[id] = struct{}{}
			}
			if len(changed) > 0 && attempt == 0 {
				for id := range verificationFailed {
					if _, retrying := changed[id]; !retrying {
						unresolved[id] = struct{}{}
					}
				}
				slog.Info("reconciliation.typesense_candidates_changed_retry", "changed", len(changed))
				pending = changed
				continue
			}
			for id := range changed {
				verificationFailed[id] = struct{}{}
			}
			for id := range verificationFailed {
				unresolved[id] = struct{}{}
			}
			break
		}
		return nil
	})
	if err != nil {
		return 0, len(candidates), err
	}
	return len(candidates) - len(unresolved), len(unresolved), nil
}

func reconcilePartition(ctx context.Context, source reconciliationSource, index reconciliationIndex, partition int, repair bool) (result reconciliationResult, resultErr error) {
	started := time.Now()
	result.Partition = partition
	defer func() { result.Duration = time.Since(started).Seconds() }()
	local, err := source.snapshot(ctx, partition)
	if err != nil {
		return result, err
	}
	remote, err := index.partition(ctx, partition)
	if err != nil {
		return result, err
	}
	diff := compareReconciliationSnapshots(local, remote)
	result.LocalRows, result.LocalActive = len(local), local.active()
	result.RemoteRows, result.RemoteActive = len(remote), remote.active()
	result.Missing, result.StateMismatch, result.PayloadMismatch = len(diff.Missing), len(diff.State), len(diff.Payload)
	result.RemoteOnlyActive, result.RemoteOnlyInactive = len(diff.RemoteActive), len(diff.RemoteInactive)
	candidates := diff.candidates()
	result.Detected, result.Unresolved = len(candidates), len(candidates)
	if repair && len(candidates) != 0 {
		result.Repaired, result.Unresolved, err = repairReconciliationCandidates(ctx, source, index, candidates, partition)
		if err != nil {
			return result, err
		}
	}
	slog.Info("reconciliation.partition", "target", "typesense", "partition", partition,
		"repair", repair, "local_rows", result.LocalRows, "local_active", result.LocalActive,
		"remote_rows", result.RemoteRows, "remote_active", result.RemoteActive,
		"missing_remote", result.Missing, "state_mismatch", result.StateMismatch,
		"payload_mismatch", result.PayloadMismatch, "remote_only_active", result.RemoteOnlyActive,
		"remote_only_inactive", result.RemoteOnlyInactive, "repaired", result.Repaired,
		"unresolved", result.Unresolved, "duration_s", time.Since(started).Seconds())
	return result, nil
}

// Called only after all 256 partitions have completed their verified writes.
// A valid local UUID without its expected bucket is a failed invariant, never
// permission to delete local truth. Require a second complete export after
// removing remote-only candidates before advancing bootstrap_complete.
func bootstrapReconciliation(ctx context.Context, source reconciliationSource, index reconciliationIndex) (active, inactive, deleted int, resultErr error) {
	resultErr = source.fence(ctx, func() error {
		var err error
		active, inactive, deleted, err = bootstrapReconciliationFenced(ctx, source, index)
		return err
	})
	return
}

func bootstrapReconciliationFenced(ctx context.Context, source reconciliationSource, index reconciliationIndex) (active, inactive, deleted int, resultErr error) {
	candidates, err := index.unbucketed(ctx)
	if err != nil {
		return 0, 0, 0, err
	}
	for start := 0; start < len(candidates); start += 500 {
		batch := candidates[start:min(start+500, len(candidates))]
		valid, raw := make([]string, 0, len(batch)), make([]string, 0, len(batch))
		for _, candidate := range batch {
			raw = append(raw, candidate.ID)
			if parsed, err := reconciliationUUID(candidate.ID); err == nil {
				valid = append(valid, parsed)
			}
		}
		if len(valid) != 0 {
			existing, err := source.existing(ctx, valid)
			if err != nil {
				return active, inactive, deleted, err
			}
			if len(existing) != 0 {
				return active, inactive, deleted, errors.New("Typesense bootstrap found local documents without buckets")
			}
		}
		if err := index.deleteIDs(ctx, raw); err != nil {
			return active, inactive, deleted, err
		}
		for _, candidate := range batch {
			if candidate.Active {
				active++
			} else {
				inactive++
			}
		}
		deleted += len(batch)
	}
	remaining, err := index.unbucketed(ctx)
	if err != nil {
		return active, inactive, deleted, err
	}
	if len(remaining) != 0 {
		return active, inactive, deleted, errors.New("Typesense bootstrap verification found unbucketed documents")
	}
	return active, inactive, deleted, nil
}
