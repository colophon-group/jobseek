//go:build densitybench

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

const densityTestCommit = "0123456789abcdef0123456789abcdef01234567"

func loadDensityTestWorkload(t *testing.T) (densityWorkload, string) {
	t.Helper()
	workload, workloadSHA, err := loadDensityWorkload("density/workload.v1.json")
	if err != nil {
		t.Fatal(err)
	}
	return workload, workloadSHA
}

func TestDensityWorkloadIsExactUniqueAndCounterbalanced(t *testing.T) {
	workload, workloadSHA := loadDensityTestWorkload(t)
	if len(workloadSHA) != 64 {
		t.Fatalf("hash=%q", workloadSHA)
	}
	seenBodies := map[string]bool{}
	for _, wave := range workload.Waves {
		for _, task := range wave.Tasks {
			if seenBodies[task.ResponseBody] {
				t.Fatalf("duplicate body for %s", task.ID)
			}
			seenBodies[task.ResponseBody] = true
			if task.Mode == "b1" {
				want, _ := json.Marshal(task.ID)
				if task.ExpectedEvaluationJSON == nil || *task.ExpectedEvaluationJSON != string(want) {
					t.Fatalf("evaluation for %s", task.ID)
				}
			}
		}
	}
	modified := append([]byte(nil), embeddedDensityWorkload...)
	modified[10] ^= 1
	path := t.TempDir() + "/workload.json"
	if err := osWriteFileForDensityTest(path, modified); err != nil {
		t.Fatal(err)
	}
	if _, _, err := loadDensityWorkload(path); err == nil {
		t.Fatal("modified workload accepted")
	}
}

func TestDensityConcurrencyIsFixed(t *testing.T) {
	for _, concurrency := range []int{1, 4, 8} {
		if !densityAllowedConcurrency(concurrency) {
			t.Fatalf("rejected concurrency %d", concurrency)
		}
	}
	for _, concurrency := range []int{-1, 0, 2, 16} {
		if densityAllowedConcurrency(concurrency) {
			t.Fatalf("accepted concurrency %d", concurrency)
		}
	}
}

func TestDensityStartGateWaitsForARegularFile(t *testing.T) {
	path := t.TempDir() + "/start"
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	go func() {
		time.Sleep(20 * time.Millisecond)
		_ = os.WriteFile(path, nil, 0o600)
	}()
	if err := waitDensityStartGate(ctx, path); err != nil {
		t.Fatal(err)
	}
	if err := waitDensityStartGate(ctx, t.TempDir()); err == nil {
		t.Fatal("directory accepted as start gate")
	}
}

func TestDensityStartGateHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := waitDensityStartGate(ctx, t.TempDir()+"/absent"); !errors.Is(err, context.Canceled) {
		t.Fatalf("err=%v", err)
	}
}

// Kept as a variable-sized helper so test writes remain local and obvious.
func osWriteFileForDensityTest(path string, value []byte) error {
	return os.WriteFile(path, value, 0o600)
}

func TestDensityRawPrivacyPreservesExactAdapterBytes(t *testing.T) {
	raw := []byte("\"ASCII value\"")
	envelope, err := (densityRawPrivacy{}).SealEvaluation(context.Background(), "id", raw, uint64(len(raw)))
	if err != nil || !bytes.Equal(envelope.Payload, raw) || envelope.PayloadSha256 != densitySHA(raw) {
		t.Fatalf("envelope=%v err=%v", envelope, err)
	}
	raw[1] = 'X'
	if string(envelope.Payload) != "\"ASCII value\"" {
		t.Fatal("privacy envelope aliased caller bytes")
	}
}

func TestDensityBenchmarkRunsSequentialWavesOnOneC4Pool(t *testing.T) {
	workload, workloadSHA := loadDensityTestWorkload(t)
	runner := newDensityFixtureRunner(workload, 4)
	adapter, err := lightpandaadapter.New(runner, densityRawPrivacy{})
	if err != nil {
		t.Fatal(err)
	}
	report := runDensityBenchmark(context.Background(), workload, workloadSHA, 4, densityTestCommit, "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", adapter)
	if !report.Succeeded || !report.ConservationOK || !report.OracleOK || !report.MaxInFlightOK || !report.ZeroPanicResidue {
		t.Fatalf("report=%+v", report)
	}
	if report.Submitted != 16 || report.Accepted != 16 || report.Terminal != 16 || report.SucceededJobs != 16 || report.FailedJobs != 0 || report.OracleMatches != 16 || report.Pool.MaxInFlight != 4 {
		t.Fatalf("counts=%+v", report)
	}
	if len(report.Waves) != 2 || !report.Waves[0].Succeeded || !report.Waves[1].Succeeded || runner.outOfOrder {
		t.Fatalf("waves=%+v out_of_order=%v", report.Waves, runner.outOfOrder)
	}
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"cpu_user", "cpu_system", "rss", "open_fds", "cgroup", "queue_ms", "service_ms", "total_queue", "total_service"} {
		if bytes.Contains(encoded, []byte(forbidden)) {
			t.Fatalf("asymmetric diagnostic %q in report: %s", forbidden, encoded)
		}
	}
	if bytes.Count(encoded, []byte(`"elapsed_ms"`)) != 1 {
		t.Fatalf("elapsed_ms must be whole-arm only: %s", encoded)
	}
}

func TestDensityFailureReportDoesNotSerializeProviderTextOrTargets(t *testing.T) {
	task := densityTask{ID: "safe", Mode: "b0"}
	report := densityJobResult(task, PoolResult{JobID: "safe", Err: errors.New("http://secret.invalid/?token=value")})
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "secret") || strings.Contains(string(encoded), "token") || strings.Contains(string(encoded), "http") {
		t.Fatalf("leak: %s", encoded)
	}
	if report.FailureID != "pool_error" {
		t.Fatalf("report=%+v", report)
	}
}

type densityFixtureRunner struct {
	mu         sync.Mutex
	tasks      map[string]densityTask
	gates      map[string]chan struct{}
	active     map[string]int
	completed  map[string]int
	opened     map[string]bool
	barrier    int
	outOfOrder bool
}

func newDensityFixtureRunner(workload densityWorkload, barrier int) *densityFixtureRunner {
	r := &densityFixtureRunner{tasks: map[string]densityTask{}, gates: map[string]chan struct{}{"w0": make(chan struct{}), "w1": make(chan struct{})}, active: map[string]int{}, completed: map[string]int{}, opened: map[string]bool{}, barrier: barrier}
	for _, wave := range workload.Waves {
		for _, task := range wave.Tasks {
			r.tasks["http://"+task.OriginID+".bench.test:8080"+task.RelativePath] = task
		}
	}
	return r
}

func (r *densityFixtureRunner) Run(_ context.Context, bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
	input := bound.Input()
	task := r.tasks[input.Plan.TargetUrl]
	wave := task.ID[:2]
	r.mu.Lock()
	if wave == "w1" && r.completed["w0"] != 8 {
		r.outOfOrder = true
	}
	r.active[wave]++
	if r.active[wave] == r.barrier && !r.opened[wave] {
		close(r.gates[wave])
		r.opened[wave] = true
	}
	gate := r.gates[wave]
	r.mu.Unlock()
	<-gate
	status := uint32(task.Status)
	raw := &lightpandaadapter.RawSuccess{FinalURL: input.Plan.TargetUrl, Status: &status, HTML: []byte(strings.TrimPrefix(task.ResponseBody, "<!doctype html>"))}
	if task.Mode == "b1" {
		raw.Evaluations = []lightpandaadapter.RawEvaluation{{EvaluationID: input.Plan.Evaluations[0].EvaluationId, JSON: []byte(*task.ExpectedEvaluationJSON)}}
	}
	r.mu.Lock()
	r.active[wave]--
	r.completed[wave]++
	r.mu.Unlock()
	return lightpandaadapter.NewRunnerSuccess(bound, raw)
}

var _ lightpandaadapter.Runner = (*densityFixtureRunner)(nil)
