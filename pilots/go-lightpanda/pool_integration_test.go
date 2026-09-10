package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"sync"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

func TestLightpandaPoolRuntimeV1IntegrationC4(t *testing.T) {
	expectedSHA256, supported := lightpandaStable040SHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("stable Lightpanda 0.4.0 integration binary requires Linux amd64 or arm64")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expectedSHA256); err != nil {
		t.Fatal(err)
	}

	const (
		workerCount = 4
		originCount = 8
	)
	observer := newPoolFixtureObserver(workerCount)
	servers := make([]*httptest.Server, 0, originCount)
	for originID := range originCount {
		originID := originID
		server := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			observer.serve(originID, writer, request)
		}))
		servers = append(servers, server)
	}

	privacy := &lockedBridgePrivacy{}
	adapter, err := lightpandaadapter.New(
		runtimeV1Runner{
			config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()},
			run:    testOnlyFixtureRunner(binary, "127.0.0.2"),
		},
		privacy,
	)
	if err != nil {
		t.Fatal(err)
	}
	pool, err := NewLightpandaPool(adapter, PoolConfig{
		Workers:        workerCount,
		Capacity:       originCount * 2,
		ResultCapacity: originCount * 2,
		JobTimeout:     30 * time.Second,
		ShutdownGrace:  30 * time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}

	for originID, server := range servers {
		for _, mode := range []string{"b0", "b1"} {
			expression := ""
			if mode == "b1" {
				expression = "document.title"
			}
			input := bridgeInput(fmt.Sprintf("%s/fixture/%d", server.URL, originID), expression, 1024)
			input.Plan.Navigation.TimeoutMs = uint64(defaultTaskTimeout / time.Millisecond)
			submitPoolJob(t, pool, PoolJob{
				ID:      fmt.Sprintf("origin-%d-%s", originID, mode),
				Timeout: 25 * time.Second,
				Input:   input,
			})
		}
	}
	pool.Close()
	results := collectPoolResultsWithTimeout(t, pool, 90*time.Second)
	if len(results) != originCount*2 {
		t.Fatalf("terminal results = %d, want %d", len(results), originCount*2)
	}

	seen := make(map[string]bool, len(results))
	for _, result := range results {
		if seen[result.JobID] {
			t.Fatalf("duplicate terminal result for %s", result.JobID)
		}
		seen[result.JobID] = true
		if result.Err != nil || result.Runtime.GetSuccess() == nil {
			t.Fatalf("job %s failed: err=%v runtime=%v", result.JobID, result.Err, result.Runtime)
		}
		var originID int
		var mode string
		if _, err := fmt.Sscanf(result.JobID, "origin-%d-%s", &originID, &mode); err != nil {
			t.Fatalf("parse job ID %q: %v", result.JobID, err)
		}
		success := result.Runtime.GetSuccess()
		wantURL := fmt.Sprintf("%s/fixture/%d", servers[originID].URL, originID)
		wantHTML := fmt.Sprintf(
			`<html><head><title>Origin %d</title></head><body><main id="origin-%d">local only</main></body></html>`,
			originID, originID,
		)
		if success.GetStatus() != http.StatusCreated || success.FinalUrl != wantURL ||
			!bytes.Equal(bridgeManifestBody(success.Html), []byte(wantHTML)) {
			t.Fatalf("job %s unexpected success: %v", result.JobID, success)
		}
		if mode == "b0" {
			if len(success.Evaluations) != 0 {
				t.Fatalf("B0 job %s returned evaluations: %v", result.JobID, success.Evaluations)
			}
		} else {
			want := fmt.Sprintf(`"Origin %d"`, originID)
			if len(success.Evaluations) != 1 || string(success.Evaluations[0].Value.Payload) != want {
				t.Fatalf("B1 job %s evaluations = %v, want %s", result.JobID, success.Evaluations, want)
			}
		}
	}

	maximumGlobal, maximumByOrigin, requestsByOrigin, unexpected, barrierTimedOut := observer.snapshot()
	if barrierTimedOut || unexpected != 0 || maximumGlobal != workerCount {
		t.Fatalf("fixture global evidence: max=%d unexpected=%d barrier_timeout=%v", maximumGlobal, unexpected, barrierTimedOut)
	}
	for originID := range originCount {
		if maximumByOrigin[originID] != 1 || requestsByOrigin[originID] != 2 {
			t.Fatalf("origin %d evidence: max=%d requests=%d", originID, maximumByOrigin[originID], requestsByOrigin[originID])
		}
	}
	if privacy.callCount() != originCount {
		t.Fatalf("privacy calls = %d, want %d", privacy.callCount(), originCount)
	}
	stats := pool.Stats()
	if stats.Accepted != originCount*2 || stats.Completed != stats.Accepted || stats.Panics != 0 ||
		stats.Queued != 0 || stats.InFlight != 0 || stats.MaxInFlight != workerCount {
		t.Fatalf("pool stats = %+v", stats)
	}
}

type poolFixtureObserver struct {
	mu               sync.Mutex
	barrier          chan struct{}
	barrierOnce      sync.Once
	barrierTarget    int
	activeGlobal     int
	maximumGlobal    int
	activeByOrigin   map[int]int
	maximumByOrigin  map[int]int
	requestsByOrigin map[int]int
	unexpected       int
	barrierTimedOut  bool
}

func newPoolFixtureObserver(barrierTarget int) *poolFixtureObserver {
	return &poolFixtureObserver{
		barrier:          make(chan struct{}),
		barrierTarget:    barrierTarget,
		activeByOrigin:   make(map[int]int),
		maximumByOrigin:  make(map[int]int),
		requestsByOrigin: make(map[int]int),
	}
}

func (observer *poolFixtureObserver) serve(originID int, writer http.ResponseWriter, request *http.Request) {
	wantPath := fmt.Sprintf("/fixture/%d", originID)
	if request.Method != http.MethodGet || request.URL.Path != wantPath || request.URL.RawQuery != "" {
		observer.mu.Lock()
		observer.unexpected++
		observer.mu.Unlock()
		http.NotFound(writer, request)
		return
	}

	observer.mu.Lock()
	observer.activeGlobal++
	observer.activeByOrigin[originID]++
	observer.requestsByOrigin[originID]++
	observer.maximumGlobal = max(observer.maximumGlobal, observer.activeGlobal)
	observer.maximumByOrigin[originID] = max(observer.maximumByOrigin[originID], observer.activeByOrigin[originID])
	if observer.activeGlobal == observer.barrierTarget {
		observer.barrierOnce.Do(func() { close(observer.barrier) })
	}
	observer.mu.Unlock()

	select {
	case <-observer.barrier:
	case <-time.After(15 * time.Second):
		observer.mu.Lock()
		observer.barrierTimedOut = true
		observer.mu.Unlock()
	}
	// Keep the first wave overlapping after all four requests reached the
	// fixture so both the global and per-origin observations are unambiguous.
	time.Sleep(25 * time.Millisecond)

	observer.mu.Lock()
	observer.activeGlobal--
	observer.activeByOrigin[originID]--
	observer.mu.Unlock()

	writer.Header().Set("Content-Type", "text/html; charset=utf-8")
	writer.WriteHeader(http.StatusCreated)
	_, _ = io.WriteString(writer, fmt.Sprintf(
		`<!doctype html><html><head><title>Origin %d</title></head><body><main id="origin-%d">local only</main></body></html>`,
		originID, originID,
	))
}

func (observer *poolFixtureObserver) snapshot() (int, map[int]int, map[int]int, int, bool) {
	observer.mu.Lock()
	defer observer.mu.Unlock()
	maximumByOrigin := make(map[int]int, len(observer.maximumByOrigin))
	for origin, maximum := range observer.maximumByOrigin {
		maximumByOrigin[origin] = maximum
	}
	requestsByOrigin := make(map[int]int, len(observer.requestsByOrigin))
	for origin, requests := range observer.requestsByOrigin {
		requestsByOrigin[origin] = requests
	}
	return observer.maximumGlobal, maximumByOrigin, requestsByOrigin, observer.unexpected, observer.barrierTimedOut
}

type lockedBridgePrivacy struct {
	mu    sync.Mutex
	calls int
}

func (privacy *lockedBridgePrivacy) SealEvaluation(
	ctx context.Context,
	evaluationID string,
	raw []byte,
	limit uint64,
) (*runtimev1.ExtensionEnvelope, error) {
	privacy.mu.Lock()
	defer privacy.mu.Unlock()
	fixture := &bridgeFixturePrivacy{}
	envelope, err := fixture.SealEvaluation(ctx, evaluationID, raw, limit)
	if err == nil {
		privacy.calls++
	}
	return envelope, err
}

func (privacy *lockedBridgePrivacy) callCount() int {
	privacy.mu.Lock()
	defer privacy.mu.Unlock()
	return privacy.calls
}

func collectPoolResultsWithTimeout(t *testing.T, pool *LightpandaPool, timeout time.Duration) []PoolResult {
	t.Helper()
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	var results []PoolResult
	for {
		select {
		case result, ok := <-pool.Results():
			if !ok {
				return results
			}
			results = append(results, result)
		case <-deadline.C:
			t.Fatal("timed out draining integration results")
		}
	}
}
