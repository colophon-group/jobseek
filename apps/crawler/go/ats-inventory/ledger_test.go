package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestLedgerReadsAndUpdatesPythonSQLite(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_ledger.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		Items          []githubWorkItem
		Reconciliation ledgerReconciliation
		AfterReconcile map[string]any `json:"after_reconcile"`
		Candidate      inventoryCandidate
		RecordItem     githubWorkItem `json:"record_item"`
		AfterRecord    map[string]any `json:"after_record"`
	}
	if e = json.Unmarshal(raw, &oracle); e != nil {
		t.Fatal(e)
	}
	db, e := os.ReadFile("testdata/python-ledger.sqlite")
	if e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(t.TempDir(), "ledger.sqlite")
	if e = os.WriteFile(path, db, 0600); e != nil {
		t.Fatal(e)
	}
	l, e := openCandidateLedger(path)
	if e != nil {
		t.Fatal(e)
	}
	defer l.Close()
	l.Now = func() time.Time { return time.Unix(1792267200, 0) }
	ctx := context.Background()
	got, e := l.reconcileRemote(ctx, oracle.Items)
	if e != nil || !reflect.DeepEqual(got, oracle.Reconciliation) {
		t.Fatalf("Python reconciliation differs: %#v %v", got, e)
	}
	assertSnapshot := func(want map[string]any) {
		t.Helper()
		count, e := l.countCreatedSince(ctx, 1792195200)
		if e != nil {
			t.Fatal(e)
		}
		value := map[string]any{"sources": l.Sources, "url_records": l.URLs, "created_today": count}
		if !reflect.DeepEqual(normalizedJSON(t, value), normalizedJSON(t, want)) {
			t.Fatalf("Python ledger snapshot differs\ngot %#v\nwant %#v", value, want)
		}
	}
	assertSnapshot(oracle.AfterReconcile)
	if e = l.recordCreated(ctx, oracle.Candidate, oracle.RecordItem); e != nil {
		t.Fatal(e)
	}
	assertSnapshot(oracle.AfterRecord)
	bad := oracle.RecordItem
	bad.Kind = "pr"
	if e = l.recordCreated(ctx, oracle.Candidate, bad); e == nil {
		t.Fatal("contributor PR marker became durable candidate")
	}
	assertSnapshot(oracle.AfterRecord)
	if _, ok := l.findSource(oracle.Candidate.SourceKey); !ok || len(l.findURL(oracle.Candidate.BoardURL)) != 1 {
		t.Fatal("ledger dedup index unavailable")
	}
}
