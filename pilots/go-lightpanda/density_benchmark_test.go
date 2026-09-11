//go:build densitybench

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"strings"
	"sync"
	"syscall"
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

// Kept as a variable-sized helper so test writes remain local and obvious.
func osWriteFileForDensityTest(path string, value []byte) error {
	return os.WriteFile(path, value, 0o600)
}

func TestDensityCLIWaitsForInitialAndFinalReleasesAroundWork(t *testing.T) {
	workloadPath := t.TempDir() + "/workload.json"
	var output bytes.Buffer
	events := []string{}
	waitInitial := func(ctx context.Context) error {
		events = append(events, "initial")
		if output.Len() != 0 {
			t.Fatalf("output emitted before initial release: %q", output.String())
		}
		if ctx.Err() != nil {
			t.Fatal(ctx.Err())
		}
		if err := os.WriteFile(workloadPath, []byte("not the manifest"), 0o600); err != nil {
			t.Fatal(err)
		}
		return nil
	}
	waitFinal := func(ctx context.Context) error {
		events = append(events, "final")
		if output.Len() != 0 {
			t.Fatalf("output emitted before final release")
		}
		if ctx.Err() != nil {
			t.Fatal(ctx.Err())
		}
		return nil
	}
	exitCode := runDensityCLIWithProtocol([]string{
		"--workload", workloadPath,
		"--concurrency", "4",
		"--source-commit", densityTestCommit,
		"--image-identity", "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
	}, &output, waitInitial, waitFinal)
	if exitCode != 2 || !slices.Equal(events, []string{"initial", "final"}) {
		t.Fatalf("exit=%d events=%v output=%q", exitCode, events, output.String())
	}
	var report densityReport
	if json.Unmarshal(output.Bytes(), &report) != nil || report.FailureID != "manifest_invalid" || report.ElapsedNS <= 0 {
		t.Fatalf("report=%+v output=%q", report, output.String())
	}
	if bytes.Count(output.Bytes(), []byte(`"elapsed_ns"`)) != 1 || bytes.Contains(output.Bytes(), []byte(`"elapsed_ms"`)) {
		t.Fatalf("failure report must expose only elapsed_ns: %s", output.Bytes())
	}
}

func TestDensityCLIRemovedStartGateUsesBothProtocolPhases(t *testing.T) {
	var output bytes.Buffer
	initialReleases := 0
	finalReleases := 0
	waitInitial := func(context.Context) error {
		initialReleases++
		if output.Len() != 0 {
			t.Fatalf("output emitted before initial release")
		}
		return nil
	}
	waitFinal := func(context.Context) error {
		finalReleases++
		return nil
	}
	exitCode := runDensityCLIWithProtocol([]string{"--start-gate", "/tmp/controller-start"}, &output, waitInitial, waitFinal)
	if exitCode != 2 || initialReleases != 1 || finalReleases != 1 {
		t.Fatalf("exit=%d initial=%d final=%d output=%q", exitCode, initialReleases, finalReleases, output.String())
	}
	var report densityReport
	if json.Unmarshal(output.Bytes(), &report) != nil || report.FailureID != "arguments_invalid" {
		t.Fatalf("report=%+v output=%q", report, output.String())
	}
}

func TestDensityCLIProtocolCancellationEmitsNoEvidence(t *testing.T) {
	workloadPath := t.TempDir() + "/workload.json"
	var output bytes.Buffer
	waitInitial := func(context.Context) error {
		return os.WriteFile(workloadPath, []byte("not the manifest"), 0o600)
	}
	waitFinal := func(context.Context) error {
		return context.Canceled
	}
	exitCode := runDensityCLIWithProtocol([]string{
		"--workload", workloadPath,
		"--concurrency", "4",
		"--source-commit", densityTestCommit,
		"--image-identity", "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
	}, &output, waitInitial, waitFinal)
	if exitCode != 1 || output.Len() != 0 {
		t.Fatalf("exit=%d output=%q", exitCode, output.String())
	}
}

func TestDensityProtocolMarkerIsExactExclusiveAndRemoved(t *testing.T) {
	path := t.TempDir() + "/controller-final"
	if err := densityCreateMarkerAt(path); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 || info.Size() != 0 ||
		metadata.Nlink != 1 || int(metadata.Uid) != os.Geteuid() || int(metadata.Gid) != os.Getegid() {
		t.Fatalf("marker=%+v metadata=%+v", info, metadata)
	}
	if err := densityCreateMarkerAt(path); err == nil {
		t.Fatal("exclusive marker creation accepted an existing path")
	}
	if err := densityRemoveMarkerAt(path); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatalf("marker remained after release: %v", err)
	}
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
	if report.ElapsedNS <= 0 {
		t.Fatalf("elapsed_ns=%d", report.ElapsedNS)
	}
	if bytes.Count(encoded, []byte(`"elapsed_ns"`)) != 1 || bytes.Contains(encoded, []byte(`"elapsed_ms"`)) {
		t.Fatalf("elapsed_ns must be whole-arm only: %s", encoded)
	}
}

func TestDensityFailureReportUsesNanosecondsFromItsMeasuredInterval(t *testing.T) {
	started := time.Now()
	time.Sleep(time.Millisecond)
	report := densityFailureReport(started, 4, densityTestCommit, "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "manifest_invalid")
	if report.ElapsedNS < int64(time.Millisecond) {
		t.Fatalf("elapsed_ns=%d", report.ElapsedNS)
	}
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Count(encoded, []byte(`"elapsed_ns"`)) != 1 || bytes.Contains(encoded, []byte(`"elapsed_ms"`)) {
		t.Fatalf("failure report must expose only elapsed_ns: %s", encoded)
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
