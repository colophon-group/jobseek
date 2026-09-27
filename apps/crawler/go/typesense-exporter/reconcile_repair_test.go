package main

import (
	"context"
	"errors"
	"testing"
)

type reconciliationRepairFixture struct {
	local, remote   map[string]map[string]any
	locked          bool
	writes, deletes int
	afterWrite      func()
	writeError      error
	rejected        reconciliationIDs
}

func repairFixture() *reconciliationRepairFixture {
	return &reconciliationRepairFixture{local: map[string]map[string]any{}, remote: map[string]map[string]any{}}
}

func fixtureReconciliationDocument(id, title string) map[string]any {
	return map[string]any{"id": id, "is_active": true, "title": title, "reconciliation_bucket": id[:2]}
}
func cloneReconciliationDocument(document map[string]any) map[string]any {
	copy := map[string]any{}
	for key, value := range document {
		copy[key] = value
	}
	return copy
}

func (f *reconciliationRepairFixture) fence(_ context.Context, work func() error) error {
	if f.locked {
		return errors.New("recursive fixture fence")
	}
	f.locked = true
	defer func() { f.locked = false }()
	return work()
}
func (f *reconciliationRepairFixture) snapshot(ctx context.Context, _ int) (reconciliationSnapshot, error) {
	ids := make([]string, 0, len(f.local))
	for id := range f.local {
		ids = append(ids, id)
	}
	_, snapshot, err := f.documents(ctx, ids)
	return snapshot, err
}
func (f *reconciliationRepairFixture) documents(_ context.Context, ids []string) ([]map[string]any, reconciliationSnapshot, error) {
	documents, snapshot := []map[string]any{}, reconciliationSnapshot{}
	for _, id := range ids {
		if document, found := f.local[id]; found {
			copy := cloneReconciliationDocument(document)
			documents = append(documents, copy)
			if err := addReconciliationDocument(snapshot, copy); err != nil {
				return nil, nil, err
			}
		}
	}
	return documents, snapshot, nil
}
func (f *reconciliationRepairFixture) existing(_ context.Context, ids []string) (reconciliationIDs, error) {
	result := reconciliationIDs{}
	for _, id := range ids {
		if _, found := f.local[id]; found {
			result[id] = struct{}{}
		}
	}
	return result, nil
}
func (f *reconciliationRepairFixture) partition(_ context.Context, _ int) (reconciliationSnapshot, error) {
	snapshot := reconciliationSnapshot{}
	for _, document := range f.remote {
		if err := addReconciliationDocument(snapshot, document); err != nil {
			return nil, err
		}
	}
	return snapshot, nil
}
func (f *reconciliationRepairFixture) upsert(_ context.Context, documents []map[string]any) (reconciliationIDs, error) {
	if !f.locked {
		return nil, errors.New("write escaped fixture fence")
	}
	f.writes++
	if f.writeError != nil {
		return nil, f.writeError
	}
	for _, document := range documents {
		f.remote[document["id"].(string)] = cloneReconciliationDocument(document)
	}
	if f.afterWrite != nil {
		f.afterWrite()
	}
	return f.rejected, nil
}
func (f *reconciliationRepairFixture) deleteIDs(_ context.Context, ids []string) error {
	if !f.locked {
		return errors.New("delete escaped fixture fence")
	}
	for _, id := range ids {
		delete(f.remote, id)
		f.deletes++
	}
	return nil
}
func (f *reconciliationRepairFixture) unbucketed(context.Context) ([]reconciliationUnbucketed, error) {
	return nil, nil
}

func TestReconciliationRepairsOnlyFrozenCandidatesAndRereadsChanges(t *testing.T) {
	ctx := context.Background()
	for _, churn := range []bool{false, true} {
		f := repairFixture()
		id := reconciliationFixtureID
		f.local[id] = fixtureReconciliationDocument(id, "old")
		f.afterWrite = func() {
			if f.writes == 1 {
				f.local[id]["title"] = "new"
			} else if churn {
				f.local[id]["title"] = "newer"
			}
			other := "a1000000-0000-0000-0000-000000000099"
			f.local[other] = fixtureReconciliationDocument(other, "unrelated new source row")
		}
		repaired, unresolved, err := repairReconciliationCandidates(ctx, f, f, reconciliationIDs{id: {}}, 0xa1)
		if err != nil || f.locked || f.writes != 2 {
			t.Fatalf("repair failed: %v", err)
		}
		if churn {
			if repaired != 0 || unresolved != 1 {
				t.Fatal("continuous churn claimed success")
			}
		} else if repaired != 1 || unresolved != 0 || f.remote[id]["title"] != "new" || len(f.remote) != 1 {
			t.Fatal("stable reread was not repaired exactly")
		}
	}
}

func TestReconciliationDeleteAndErrorRetainCandidateAccounting(t *testing.T) {
	ctx := context.Background()
	id := reconciliationFixtureID
	f := repairFixture()
	f.remote[id] = fixtureReconciliationDocument(id, "orphan")
	result, err := reconcilePartition(ctx, f, f, 0xa1, true)
	if err != nil || result.Repaired != 1 || result.Unresolved != 0 || f.deletes != 1 || f.locked {
		t.Fatalf("orphan repair: %+v %v", result, err)
	}
	f = repairFixture()
	f.local[id] = fixtureReconciliationDocument(id, "missing")
	f.writeError = errors.New("ambiguous acknowledgement")
	result, err = reconcilePartition(ctx, f, f, 0xa1, true)
	if err == nil || result.Repaired != 0 || result.Unresolved != 1 || result.Detected != 1 || f.locked {
		t.Fatalf("failure lost accounting: %+v %v", result, err)
	}
}

type reconciliationBootstrapFixture struct {
	*reconciliationRepairFixture
	candidates []reconciliationUnbucketed
}

func (f *reconciliationBootstrapFixture) unbucketed(context.Context) ([]reconciliationUnbucketed, error) {
	return f.candidates, nil
}

func TestReconciliationRejectsPoisonAndProtectsLocalBootstrapCandidates(t *testing.T) {
	ctx := context.Background()
	f := repairFixture()
	f.local[reconciliationFixtureID] = fixtureReconciliationDocument(reconciliationFixtureID, "source")
	f.rejected = reconciliationIDs{reconciliationFixtureID: {}}
	repaired, unresolved, err := repairReconciliationCandidates(ctx, f, f, f.rejected, 0xa1)
	if err != nil || repaired != 0 || unresolved != 1 {
		t.Fatal("rejected acknowledgement was ignored after matching readback")
	}
	index := &reconciliationBootstrapFixture{reconciliationRepairFixture: f, candidates: []reconciliationUnbucketed{{ID: reconciliationFixtureID, Active: true}}}
	if _, _, _, err := bootstrapReconciliation(ctx, f, index); err == nil || f.deletes != 0 || f.locked {
		t.Fatal("bootstrap deleted local truth or retained its fence")
	}
}

func TestReconciliationReadOnlyDoesNotWriteAndOptionsAreBounded(t *testing.T) {
	f := repairFixture()
	f.local[reconciliationFixtureID] = fixtureReconciliationDocument(reconciliationFixtureID, "missing")
	result, err := reconcilePartition(context.Background(), f, f, 0xa1, false)
	if err != nil || result.Unresolved != 1 || f.writes != 0 || f.deletes != 0 {
		t.Fatal("dry-run mutated index")
	}
	for _, args := range [][]string{{"--fresh-cycle"}, {"--max-partitions", "0"}, {"--max-partitions", "257"}, {"--start-partition", "256"}, {"--target", "supabase"}, {"--candidate-order-benchmark-sha256", "bad"}, {"unexpected"}} {
		if _, err := parseReconciliationOptions(args); err == nil {
			t.Fatalf("accepted options %v", args)
		}
	}
	if _, err := parseReconciliationOptions([]string{"--repair", "--full", "--fresh-cycle", "--target", "typesense"}); err != nil {
		t.Fatal(err)
	}
}
