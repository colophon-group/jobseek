package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

func testPoolConfig(workers, capacity int) PoolConfig {
	return PoolConfig{
		Workers:        workers,
		Capacity:       capacity,
		ResultCapacity: capacity,
		JobTimeout:     time.Second,
		ShutdownGrace:  time.Second,
	}
}

func testPoolJob(id, target string) PoolJob {
	return PoolJob{ID: id, Input: bridgeInput(target, "", 0)}
}

func submitPoolJob(t *testing.T, pool *LightpandaPool, job PoolJob) {
	t.Helper()
	if err := pool.Submit(context.Background(), job); err != nil {
		t.Fatalf("submit %s: %v", job.ID, err)
	}
}

func collectPoolResults(t *testing.T, pool *LightpandaPool) []PoolResult {
	t.Helper()
	deadline := time.NewTimer(3 * time.Second)
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
			t.Fatal("timed out draining pool results")
		}
	}
}

func waitForPool(t *testing.T, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatal("condition was not met")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestLightpandaPoolRejectsInvalidBounds(t *testing.T) {
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return &runtimev1.BrowserResult{}, nil
	})
	valid := testPoolConfig(2, 3)

	for name, mutate := range map[string]func(*PoolConfig){
		"workers":           func(config *PoolConfig) { config.Workers = 0 },
		"capacity":          func(config *PoolConfig) { config.Capacity = 1 },
		"result capacity":   func(config *PoolConfig) { config.ResultCapacity = 0 },
		"result under work": func(config *PoolConfig) { config.ResultCapacity = 2 },
		"job timeout":       func(config *PoolConfig) { config.JobTimeout = 0 },
		"shutdown grace":    func(config *PoolConfig) { config.ShutdownGrace = 0 },
	} {
		t.Run(name, func(t *testing.T) {
			config := valid
			mutate(&config)
			if _, err := NewLightpandaPoolWithProcessor(config, processor); !errors.Is(err, errInvalidPoolConfig) {
				t.Fatalf("error = %v", err)
			}
		})
	}
	if _, err := NewLightpandaPoolWithProcessor(valid, nil); !errors.Is(err, errInvalidPoolConfig) {
		t.Fatalf("nil processor error = %v", err)
	}
	if _, err := NewLightpandaPool(nil, valid); !errors.Is(err, errInvalidPoolConfig) {
		t.Fatalf("nil adapter error = %v", err)
	}
}

func TestLightpandaPoolRejectsInvalidJobsWithoutConsumingCapacity(t *testing.T) {
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return &runtimev1.BrowserResult{}, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 1), processor)
	if err != nil {
		t.Fatal(err)
	}
	valid := testPoolJob("valid", "https://valid.example/jobs")
	oversized := testPoolJob("oversized", "https://oversized.example/jobs")
	oversized.Input.Assignment.RoutingRevision = strings.Repeat("x", lightpandaadapter.InputPayloadLimit)
	invalidURL := testPoolJob("invalid-url", "https://valid.example/jobs")
	invalidURL.Input.Plan.TargetUrl = "file:///tmp/jobs"
	negativeTimeout := valid
	negativeTimeout.Timeout = -time.Millisecond

	tests := map[string]struct {
		context context.Context
		job     PoolJob
	}{
		"nil context":      {job: valid},
		"blank ID":         {context: context.Background(), job: PoolJob{ID: " ", Input: valid.Input}},
		"negative timeout": {context: context.Background(), job: negativeTimeout},
		"invalid URL":      {context: context.Background(), job: invalidURL},
		"oversized input":  {context: context.Background(), job: oversized},
	}
	for name, test := range tests {
		if err := pool.Submit(test.context, test.job); !errors.Is(err, errInvalidPoolJob) {
			t.Fatalf("%s error = %v", name, err)
		}
	}
	if stats := pool.Stats(); stats.Accepted != 0 || stats.Completed != 0 || stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("invalid jobs changed stats = %+v", stats)
	}

	submitPoolJob(t, pool, valid)
	pool.Close()
	if results := collectPoolResults(t, pool); len(results) != 1 || results[0].JobID != valid.ID {
		t.Fatalf("valid result after rejection = %+v", results)
	}
}

func TestLightpandaPoolBoundsAcceptedWork(t *testing.T) {
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		select {
		case started <- struct{}{}:
		default:
		}
		<-release
		return &runtimev1.BrowserResult{}, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 2), processor)
	if err != nil {
		t.Fatal(err)
	}

	submitPoolJob(t, pool, testPoolJob("one", "https://one.example/jobs"))
	<-started
	submitPoolJob(t, pool, testPoolJob("two", "https://two.example/jobs"))
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := pool.Submit(ctx, testPoolJob("rejected", "https://three.example/jobs")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("third submit error = %v", err)
	}

	pool.Close()
	close(release)
	results := collectPoolResults(t, pool)
	if len(results) != 2 {
		t.Fatalf("results = %d", len(results))
	}
	stats := pool.Stats()
	if stats.Accepted != 2 || stats.Completed != 2 || stats.MaxInFlight != 1 || stats.MaxQueued < 1 {
		t.Fatalf("stats = %+v", stats)
	}
}

func TestLightpandaPoolRejectsPreCanceledAdmissionDeterministically(t *testing.T) {
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return &runtimev1.BrowserResult{}, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 1), processor)
	if err != nil {
		t.Fatal(err)
	}
	admissionContext, cancel := context.WithCancel(context.Background())
	cancel()
	for attempt := range 100 {
		err := pool.Submit(admissionContext, testPoolJob(
			fmt.Sprintf("canceled-%d", attempt),
			"https://canceled-admission.example/jobs",
		))
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("attempt %d error = %v", attempt, err)
		}
	}
	if stats := pool.Stats(); stats.Accepted != 0 || stats.Completed != 0 || stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("stats before close = %+v", stats)
	}
	pool.Close()
	if results := collectPoolResults(t, pool); len(results) != 0 {
		t.Fatalf("terminal results = %d", len(results))
	}
}

func TestLightpandaPoolIsOriginFairAndSingleFlight(t *testing.T) {
	started := make(chan string, 8)
	release := make(chan struct{})
	var mu sync.Mutex
	activeByOrigin := make(map[string]int)
	maxByOrigin := make(map[string]int)
	activeTotal := 0
	maxTotal := 0

	processor := PoolProcessorFunc(func(_ context.Context, input *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		origin, err := canonicalTargetOrigin(input.Plan.TargetUrl)
		if err != nil {
			return nil, err
		}
		mu.Lock()
		activeByOrigin[origin]++
		if activeByOrigin[origin] > maxByOrigin[origin] {
			maxByOrigin[origin] = activeByOrigin[origin]
		}
		activeTotal++
		if activeTotal > maxTotal {
			maxTotal = activeTotal
		}
		mu.Unlock()
		started <- origin
		<-release
		mu.Lock()
		activeByOrigin[origin]--
		activeTotal--
		mu.Unlock()
		return &runtimev1.BrowserResult{}, nil
	})

	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(4, 8), processor)
	if err != nil {
		t.Fatal(err)
	}
	for _, origin := range []string{"a", "b", "c", "d"} {
		for wave := range 2 {
			submitPoolJob(t, pool, testPoolJob(
				fmt.Sprintf("%s-%d", origin, wave),
				fmt.Sprintf("https://%s.example/jobs", origin),
			))
		}
	}

	firstWave := make(map[string]bool)
	for range 4 {
		select {
		case origin := <-started:
			firstWave[origin] = true
		case <-time.After(time.Second):
			t.Fatal("four distinct origins did not reach service")
		}
	}
	if len(firstWave) != 4 {
		t.Fatalf("first active origins = %v", firstWave)
	}
	for range 100 {
		stats := pool.Stats()
		if stats.Queued < 0 || stats.InFlight < 0 || stats.MaxQueued < 0 || stats.MaxInFlight < 0 {
			t.Fatalf("invalid concurrent stats snapshot = %+v", stats)
		}
	}
	pool.Close()
	close(release)
	if results := collectPoolResults(t, pool); len(results) != 8 {
		t.Fatalf("results = %d", len(results))
	}

	mu.Lock()
	defer mu.Unlock()
	if maxTotal != 4 {
		t.Fatalf("maximum global concurrency = %d, want 4", maxTotal)
	}
	for origin, maximum := range maxByOrigin {
		if maximum != 1 {
			t.Fatalf("origin %s maximum concurrency = %d", origin, maximum)
		}
	}
	if stats := pool.Stats(); stats.MaxInFlight != 4 || stats.Accepted != 8 || stats.Completed != 8 ||
		stats.TotalQueueTime <= 0 || stats.MaxQueueTime <= 0 || stats.TotalServiceTime <= 0 || stats.MaxServiceTime <= 0 {
		t.Fatalf("stats = %+v", stats)
	}
}

func TestLightpandaPoolConcurrentSubmitAndCloseConservesAcceptedJobs(t *testing.T) {
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return &runtimev1.BrowserResult{}, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(4, 16), processor)
	if err != nil {
		t.Fatal(err)
	}

	resultsDone := make(chan []PoolResult, 1)
	go func() {
		var results []PoolResult
		for result := range pool.Results() {
			results = append(results, result)
		}
		resultsDone <- results
	}()
	gate := make(chan struct{})
	var accepted atomic.Uint64
	var submits sync.WaitGroup
	for index := range 64 {
		submits.Add(1)
		go func(index int) {
			defer submits.Done()
			<-gate
			err := pool.Submit(context.Background(), testPoolJob(
				fmt.Sprintf("job-%d", index),
				fmt.Sprintf("https://origin-%d.example/jobs", index),
			))
			switch {
			case err == nil:
				accepted.Add(1)
			case errors.Is(err, errPoolClosed):
			default:
				t.Errorf("submit %d error = %v", index, err)
			}
		}(index)
	}
	close(gate)
	pool.Close()
	submits.Wait()
	pool.Close()
	results := <-resultsDone
	if uint64(len(results)) != accepted.Load() {
		t.Fatalf("terminal/accepted = %d/%d", len(results), accepted.Load())
	}
	stats := pool.Stats()
	if stats.Accepted != accepted.Load() || stats.Completed != stats.Accepted || stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("stats = %+v, submit accepted = %d", stats, accepted.Load())
	}
}

func TestLightpandaPoolRejectsAmbiguousProcessorTerminal(t *testing.T) {
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 1), PoolProcessorFunc(
		func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
			return nil, nil
		},
	))
	if err != nil {
		t.Fatal(err)
	}
	submitPoolJob(t, pool, testPoolJob("ambiguous", "https://ambiguous.example/jobs"))
	pool.Close()
	results := collectPoolResults(t, pool)
	if len(results) != 1 || !errors.Is(results[0].Err, errPoolProcessor) || results[0].Runtime != nil {
		t.Fatalf("terminal result = %+v", results)
	}
}

func TestLightpandaPoolClonesAcceptedInput(t *testing.T) {
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	observed := make(chan *runtimev1.BrowserExecutionInput, 1)
	processor := PoolProcessorFunc(func(_ context.Context, input *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		if input.Plan.TargetUrl == "https://block.example/jobs" {
			close(firstStarted)
			<-releaseFirst
		} else {
			observed <- proto.Clone(input).(*runtimev1.BrowserExecutionInput)
		}
		return &runtimev1.BrowserResult{}, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 2), processor)
	if err != nil {
		t.Fatal(err)
	}
	submitPoolJob(t, pool, testPoolJob("block", "https://block.example/jobs"))
	<-firstStarted
	input := bridgeInput("https://immutable.example/jobs", "document.title", 64)
	original := proto.Clone(input).(*runtimev1.BrowserExecutionInput)
	submitPoolJob(t, pool, PoolJob{ID: "immutable", Input: input})
	input.Plan.TargetUrl = "https://caller-mutated.invalid/jobs"
	input.Plan.Evaluations[0].Expression = "secret mutation"
	close(releaseFirst)
	pool.Close()
	_ = collectPoolResults(t, pool)
	if got := <-observed; !proto.Equal(got, original) {
		t.Fatalf("processor input changed after Submit: got %v want %v", got, original)
	}
}

func TestLightpandaPoolClonesPublishedOutput(t *testing.T) {
	owned := &runtimev1.BrowserResult{ContractVersion: "processor-owned"}
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return owned, nil
	})
	pool, err := NewLightpandaPoolWithProcessor(testPoolConfig(1, 1), processor)
	if err != nil {
		t.Fatal(err)
	}
	submitPoolJob(t, pool, testPoolJob("output-clone", "https://output.example/jobs"))
	pool.Close()
	results := collectPoolResults(t, pool)
	if len(results) != 1 || results[0].Runtime == nil {
		t.Fatalf("terminal results = %+v", results)
	}

	owned.ContractVersion = "processor-mutated"
	if results[0].Runtime.ContractVersion != "processor-owned" {
		t.Fatalf("published output aliased processor output: %q", results[0].Runtime.ContractVersion)
	}
	results[0].Runtime.ContractVersion = "consumer-mutated"
	if owned.ContractVersion != "processor-mutated" {
		t.Fatalf("processor output aliased published output: %q", owned.ContractVersion)
	}
}

func TestLightpandaPoolPublishesOneTerminalPerAcceptedJob(t *testing.T) {
	processor := PoolProcessorFunc(func(ctx context.Context, input *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		switch input.Plan.TargetUrl {
		case "https://panic.example/jobs":
			panic("secret panic text")
		case "https://success.example/jobs":
			return &runtimev1.BrowserResult{ContractVersion: "success"}, nil
		default:
			<-ctx.Done()
			return nil, ctx.Err()
		}
	})
	config := testPoolConfig(4, 4)
	config.JobTimeout = 25 * time.Millisecond
	pool, err := NewLightpandaPoolWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}

	canceledContext, cancel := context.WithCancel(context.Background())
	cancel()
	jobs := []PoolJob{
		testPoolJob("panic", "https://panic.example/jobs"),
		testPoolJob("success", "https://success.example/jobs"),
		testPoolJob("timeout", "https://timeout.example/jobs"),
		testPoolJob("canceled", "https://canceled.example/jobs"),
	}
	jobs[3].Context = canceledContext
	for _, job := range jobs {
		submitPoolJob(t, pool, job)
	}
	pool.Close()
	results := collectPoolResults(t, pool)

	byID := make(map[string]PoolResult)
	for _, result := range results {
		if _, exists := byID[result.JobID]; exists {
			t.Fatalf("duplicate terminal result for %s", result.JobID)
		}
		byID[result.JobID] = result
		if result.AcceptedAt.IsZero() || result.StartedAt.Before(result.AcceptedAt) ||
			result.FinishedAt.Before(result.StartedAt) || result.QueueDuration < 0 || result.ServiceDuration < 0 {
			t.Fatalf("invalid timing: %+v", result)
		}
	}
	if len(byID) != len(jobs) {
		t.Fatalf("terminal results = %d", len(byID))
	}
	var panicErr *PoolPanicError
	if !errors.As(byID["panic"].Err, &panicErr) || strings.Contains(byID["panic"].Err.Error(), "secret") {
		t.Fatalf("panic result = %v", byID["panic"].Err)
	}
	if byID["success"].Err != nil || byID["success"].Runtime.GetContractVersion() != "success" {
		t.Fatalf("success result = %+v", byID["success"])
	}
	if !errors.Is(byID["timeout"].Err, context.DeadlineExceeded) {
		t.Fatalf("timeout result = %v", byID["timeout"].Err)
	}
	if !errors.Is(byID["canceled"].Err, context.Canceled) {
		t.Fatalf("canceled result = %v", byID["canceled"].Err)
	}
	if stats := pool.Stats(); stats.Accepted != 4 || stats.Completed != 4 || stats.Panics != 1 ||
		stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("stats = %+v", stats)
	}
}

func TestLightpandaPoolShutdownWaitIsBoundedWhenProcessorViolatesContext(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	processor := PoolProcessorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		close(started)
		<-release
		return nil, nil
	})
	config := testPoolConfig(1, 1)
	config.ShutdownGrace = 5 * time.Millisecond
	pool, err := NewLightpandaPoolWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}
	submitPoolJob(t, pool, testPoolJob("ignores-context", "https://ignores.example/jobs"))
	<-started
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := pool.Shutdown(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Shutdown error = %v", err)
	}
	close(release)
	if results := collectPoolResults(t, pool); len(results) != 1 || !errors.Is(results[0].Err, context.Canceled) {
		t.Fatalf("results = %+v", results)
	}
}

func TestLightpandaPoolOneAcceptedWindowFitsBeforeResultDrain(t *testing.T) {
	processor := PoolProcessorFunc(func(ctx context.Context, _ *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	})
	config := testPoolConfig(1, 3)
	config.JobTimeout = 5 * time.Second
	config.ShutdownGrace = 25 * time.Millisecond
	pool, err := NewLightpandaPoolWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}
	for index := range 3 {
		submitPoolJob(t, pool, testPoolJob(fmt.Sprintf("job-%d", index), "https://same.example/jobs"))
	}

	started := time.Now()
	pool.Close()
	select {
	case <-pool.Done():
	case <-time.After(500 * time.Millisecond):
		t.Fatal("one accepted window did not fit in the bounded result buffer")
	}
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("bounded shutdown took %s", elapsed)
	}
	results := collectPoolResults(t, pool)
	if len(results) != 3 {
		t.Fatalf("results = %d", len(results))
	}
	for _, result := range results {
		if !errors.Is(result.Err, context.Canceled) {
			t.Fatalf("job %s error = %v", result.JobID, result.Err)
		}
	}
	if err := pool.Submit(context.Background(), testPoolJob("late", "https://late.example/jobs")); !errors.Is(err, errPoolClosed) {
		t.Fatalf("late submit error = %v", err)
	}
}

func TestCanonicalTargetOrigin(t *testing.T) {
	t.Parallel()
	for _, test := range []struct {
		raw     string
		want    string
		wantErr bool
	}{
		{raw: "https://EXAMPLE.test:443/jobs?x=1", want: "https://example.test"},
		{raw: "http://[::1]:80/jobs", want: "http://[::1]"},
		{raw: "http://127.0.0.1:8123/jobs", want: "http://127.0.0.1:8123"},
		{raw: "https://user@example.test/jobs", wantErr: true},
		{raw: "file:///tmp/jobs", wantErr: true},
		{raw: "https://example.test/jobs#fragment", wantErr: true},
	} {
		t.Run(test.raw, func(t *testing.T) {
			t.Parallel()
			got, err := canonicalTargetOrigin(test.raw)
			if (err != nil) != test.wantErr || got != test.want {
				t.Fatalf("canonicalTargetOrigin(%q) = %q, %v", test.raw, got, err)
			}
		})
	}
}
