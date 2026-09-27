package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

type backfillFixture struct {
	t          *testing.T
	fenced     bool
	existing   cursor
	cutoff     time.Time
	positions  []cursor
	index      int
	failRead   bool
	saved      []cursor
	seenAfter  []cursor
	seenCutoff []time.Time
}

func (f *backfillFixture) Fence(_ context.Context, work func() error) error {
	f.fenced = true
	defer func() { f.fenced = false }()
	return work()
}
func (f *backfillFixture) requireFence() {
	f.t.Helper()
	if !f.fenced {
		f.t.Fatal("operation escaped the cursor fence")
	}
}
func (f *backfillFixture) Position(context.Context) (cursor, error) {
	f.requireFence()
	return f.existing, nil
}
func (f *backfillFixture) Cutoff(context.Context) (time.Time, error) {
	f.requireFence()
	return f.cutoff, nil
}
func (f *backfillFixture) Documents(_ context.Context, after cursor, cutoff time.Time) ([]map[string]any, cursor, error) {
	f.requireFence()
	f.seenAfter = append(f.seenAfter, after)
	f.seenCutoff = append(f.seenCutoff, cutoff)
	if f.failRead {
		return nil, after, errors.New("source unavailable")
	}
	if f.index == len(f.positions) {
		return nil, after, nil
	}
	next := f.positions[f.index]
	f.index++
	return []map[string]any{{"id": next.ID}}, next, nil
}
func (f *backfillFixture) Save(_ context.Context, position cursor) error {
	f.requireFence()
	f.saved = append(f.saved, position)
	return nil
}

func newBackfillFixture(t *testing.T) *backfillFixture {
	t.Helper()
	stamp := time.Date(2026, 9, 26, 12, 0, 0, 0, time.UTC)
	start, _ := parseCursor("")
	return &backfillFixture{
		t: t, existing: start, cutoff: stamp.Add(time.Minute),
		positions: []cursor{
			{UpdatedAt: stamp, ID: "00000000-0000-0000-0000-000000000001"},
			{UpdatedAt: stamp, ID: "00000000-0000-0000-0000-000000000002"},
		},
	}
}

func TestBackfillKeepsFixedCutoffAndFenceUntilAcknowledgedSave(t *testing.T) {
	f := newBackfillFixture(t)
	var written []string
	total, err := backfill(context.Background(), f, func(_ context.Context, docs []map[string]any) error {
		f.requireFence()
		if len(f.saved) != 0 {
			t.Fatal("saved cursor before finishing all batches")
		}
		written = append(written, docs[0]["id"].(string))
		return nil
	})
	if err != nil || total != 2 || f.fenced || len(f.saved) != 1 || f.saved[0] != f.positions[1] {
		t.Fatalf("backfill: total=%d saved=%v err=%v", total, f.saved, err)
	}
	if !reflect.DeepEqual(written, []string{f.positions[0].ID, f.positions[1].ID}) ||
		!reflect.DeepEqual(f.seenAfter, []cursor{f.existing, f.positions[0], f.positions[1]}) {
		t.Fatalf("wrong scan: written=%v after=%v", written, f.seenAfter)
	}
	for _, cutoff := range f.seenCutoff {
		if cutoff != f.cutoff {
			t.Fatal("cutoff changed during backfill")
		}
	}
}

func TestBackfillFailureLeavesCursorAndReplayCoversAcknowledgedPrefix(t *testing.T) {
	f := newBackfillFixture(t)
	var written []string
	write := func(_ context.Context, docs []map[string]any) error {
		written = append(written, docs[0]["id"].(string))
		if len(written) == 2 {
			return errors.New("lost acknowledgement after downstream write")
		}
		return nil
	}
	if n, err := backfill(context.Background(), f, write); err == nil || n != 1 || len(f.saved) != 0 || f.fenced {
		t.Fatalf("failed run advanced state: n=%d saved=%v err=%v", n, f.saved, err)
	}
	f.index = 0 // A restart scans from the beginning and safely upserts again.
	if n, err := backfill(context.Background(), f, write); err != nil || n != 2 || len(f.saved) != 1 {
		t.Fatalf("replay failed: n=%d saved=%v err=%v", n, f.saved, err)
	}
	if !reflect.DeepEqual(written, []string{f.positions[0].ID, f.positions[1].ID, f.positions[0].ID, f.positions[1].ID}) {
		t.Fatalf("replay skipped documents: %v", written)
	}
}

func TestBackfillDoesNotRewindCursorOrSaveAfterInvalidRead(t *testing.T) {
	for _, kind := range []string{"empty", "older-tail", "empty-initial", "read-error", "at-cutoff", "out-of-order", "cancelled"} {
		t.Run(kind, func(t *testing.T) {
			f := newBackfillFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			f.existing = cursor{UpdatedAt: f.cutoff, ID: zeroUUID}
			wantError := false
			switch kind {
			case "empty":
				f.positions = nil
			case "empty-initial":
				f.positions = nil
				f.existing, _ = parseCursor("")
			case "read-error":
				f.failRead, wantError = true, true
			case "at-cutoff":
				f.positions[0].UpdatedAt, wantError = f.cutoff, true
			case "out-of-order":
				f.positions[1], wantError = f.positions[0], true
			case "cancelled":
				cancel()
				wantError = true
			}
			_, err := backfill(ctx, f, func(context.Context, []map[string]any) error { return nil })
			if (err != nil) != wantError {
				t.Fatalf("error=%v wanted error=%v", err, wantError)
			}
			if wantError || kind == "empty-initial" {
				if len(f.saved) != 0 {
					t.Fatal("saved cursor without a complete acknowledged scan")
				}
			} else if len(f.saved) != 1 || f.saved[0] != f.existing {
				t.Fatalf("rewound cursor: %v", f.saved)
			}
		})
	}
}

func TestBackfillImportRetriesAcknowledgementFailuresAndAbortsPoison(t *testing.T) {
	for _, poison := range []bool{false, true} {
		t.Run(map[bool]string{false: "recover", true: "poison"}[poison], func(t *testing.T) {
			attempts := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				attempts++
				body, _ := io.ReadAll(r.Body)
				if r.Method != "POST" || r.URL.Query().Get("action") != "upsert" || strings.TrimSpace(string(body)) != `{"id":"one"}` {
					t.Errorf("unexpected import %s %s %s", r.Method, r.URL, body)
				}
				if poison || attempts == 2 {
					_, _ = io.WriteString(w, `{"success":false,"error":"invalid field","code":400}`)
				} else if attempts == 1 {
					_, _ = io.WriteString(w, `{}`) // Ambiguous acknowledgement.
				} else {
					_, _ = io.WriteString(w, `{"success":true}`)
				}
			}))
			defer server.Close()
			err := importBackfillBatch(context.Background(), server.Client(), server.URL, "key", []map[string]any{{"id": "one"}}, 0)
			wantAttempts := 3
			if poison {
				wantAttempts = 5
			}
			if (err != nil) != poison || attempts != wantAttempts {
				t.Fatalf("attempts=%d err=%v", attempts, err)
			}
		})
	}
}

func TestBackfillCompletionMetricsKeepGroupingAndResult(t *testing.T) {
	for _, success := range []bool{false, true} {
		called := false
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			called = true
			body, _ := io.ReadAll(r.Body)
			status := "0"
			if success {
				status = "1"
			}
			if r.Method != "PUT" || r.URL.Path != "/metrics/job/crawler-cron/cron_job/backfill-typesense" ||
				!strings.Contains(string(body), "crawler_cron_last_run_ts{job=\"backfill-typesense\"}") ||
				!strings.Contains(string(body), "crawler_cron_last_run_status{job=\"backfill-typesense\"} "+status+"\n") {
				t.Errorf("unexpected completion metrics: %s %s %s", r.Method, r.URL, body)
			}
			w.WriteHeader(http.StatusServiceUnavailable) // Observability failure is nonfatal.
		}))
		pushBackfillMetrics(server.URL, success)
		server.Close()
		if !called {
			t.Fatal("missing configured completion push")
		}
	}
}
