package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const reconciliationFixtureID = "a1000000-0000-0000-0000-000000000001"

func TestReconciliationExportRetriesWholeStreamWithoutPartialState(t *testing.T) {
	var attempts atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("filter_by") != "reconciliation_bucket:=a1" || r.Header.Get("X-TYPESENSE-API-KEY") != "test-key" {
			t.Error("missing filter/key")
		}
		attempt := attempts.Add(1)
		fmt.Fprintf(w, `{"id":%q,"is_active":true,"reconciliation_bucket":"a1","title":"A B"}`+"\n", reconciliationFixtureID)
		if attempt == 1 {
			fmt.Fprint(w, `{"id":`)
		}
	}))
	defer server.Close()
	client := reconciliationHTTP{Client: server.Client(), BaseURL: server.URL, Key: "test-key"}
	snapshot, err := client.partition(context.Background(), 0xa1)
	if err != nil || len(snapshot) != 1 || attempts.Load() != 2 {
		t.Fatalf("snapshot=%v attempts=%d error=%v", snapshot, attempts.Load(), err)
	}
}

func TestReconciliationExportFailureNeverPublishesPrefix(t *testing.T) {
	for _, test := range []struct {
		name, suffix     string
		status, attempts int
	}{
		{"truncated", `{"id":`, 200, 3},
		{"invalid UTF8", string([]byte{0xff}), 200, 3},
		{"oversized", strings.Repeat("x", reconciliationRecordLimit+1), 200, 3},
		{"non-object", `[]`, 200, 1},
		{"wrong bucket", `{"id":"a2000000-0000-0000-0000-000000000001","is_active":true,"reconciliation_bucket":"a2"}`, 200, 1},
		{"bad state", `{"id":"a1000000-0000-0000-0000-000000000002","is_active":1,"reconciliation_bucket":"a1"}`, 200, 1},
		{"transient status", "", 503, 3},
		{"permanent status", "", 401, 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			var attempts atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				attempts.Add(1)
				w.WriteHeader(test.status)
				fmt.Fprintf(w, `{"id":%q,"is_active":true,"reconciliation_bucket":"a1"}`+"\n%s", reconciliationFixtureID, test.suffix)
			}))
			defer server.Close()
			client := reconciliationHTTP{Client: server.Client(), BaseURL: server.URL}
			snapshot, err := client.partition(context.Background(), 0xa1)
			if err == nil || snapshot != nil || attempts.Load() != int32(test.attempts) {
				t.Fatalf("leaked snapshot=%v attempts=%d error=%v", snapshot, attempts.Load(), err)
			}
		})
	}
}

func TestReconciliationUnbucketedWaitsForCompleteEOF(t *testing.T) {
	var attempts atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempt := attempts.Add(1)
		fmt.Fprint(w, `{"id":"legacy/id","is_active":false}`+"\n")
		fmt.Fprintf(w, `{"id":%q,"is_active":true,"reconciliation_bucket":"a1"}`+"\n", reconciliationFixtureID)
		if attempt < 3 {
			fmt.Fprint(w, `{"is_active":`)
		}
	}))
	defer server.Close()
	client := reconciliationHTTP{Client: server.Client(), BaseURL: server.URL}
	candidates, err := client.unbucketed(context.Background())
	if err != nil || attempts.Load() != 3 || len(candidates) != 1 || candidates[0].ID != "legacy/id" {
		t.Fatalf("unexpected candidates %v, %v", candidates, err)
	}
}

func TestReconciliationDeleteCancellationWaitsForSiblings(t *testing.T) {
	var running, maximum atomic.Int32
	started := make(chan struct{}, 40)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		current := running.Add(1)
		defer running.Add(-1)
		for previous := maximum.Load(); current > previous; previous = maximum.Load() {
			if maximum.CompareAndSwap(previous, current) {
				break
			}
		}
		started <- struct{}{}
		<-r.Context().Done()
	}))
	defer server.Close()
	client := reconciliationHTTP{Client: server.Client(), BaseURL: server.URL}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	ids := make([]string, 40)
	for i := range ids {
		ids[i] = fmt.Sprintf("id/%d", i)
	}
	go func() { finished <- client.deleteIDs(ctx, ids) }()
	for i := 0; i < 20; i++ {
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("delete workers did not start")
		}
	}
	cancel()
	select {
	case err := <-finished:
		if err == nil {
			t.Fatal("cancelled deletes succeeded")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("delete workers survived cancellation")
	}
	if maximum.Load() > 20 {
		t.Fatalf("unbounded deletes: %d", maximum.Load())
	}
}
