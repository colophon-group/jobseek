package main

import (
	"context"
	"github.com/jackc/pgx/v5/pgxpool"
)

type b0CutoverAdmin struct {
	pool                  *pgxpool.Pool
	queue                 *b0ColdQueue
	producer              b0PlanProducer
	mode, markerDirectory string
	markerUID             uint32
}

type b0CutoverPlan struct {
	Operation, Cohort, Digest, RollbackDigest string
	Document                                  map[string]any
	Tasks                                     []map[string]any
}

func b0PlanFinish(operation, cohort string, document map[string]any, tasks []map[string]any, rollbackDigest string) (b0CutoverPlan, error) {
	_, digest, err := b0PlanCanonical(document)
	if err != nil {
		return b0CutoverPlan{}, err
	}
	if rollbackDigest == "" {
		rollbackDigest = digest
	}
	return b0CutoverPlan{Operation: operation, Cohort: cohort, Digest: digest, RollbackDigest: rollbackDigest, Document: document, Tasks: tasks}, nil
}

// The production implementation is the existing authenticated producer client.
// Isolated original-language fixtures supply the same two bounded operations.
type b0PlanProducer interface {
	cutoverManifest(context.Context, string) (capacityProducerDecision, error)
	cutoverTask(context.Context, string, string, string, float64, map[string]string, bool, string, string) (capacityProducerDecision, error)
}
