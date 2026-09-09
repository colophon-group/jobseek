package resident

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

const testCommit = "0123456789abcdef0123456789abcdef01234567"

func testIdentity() string {
	return "ghcr.io/colophon-group/jobseek-go-sitemap-resident-shadow:sha-" + testCommit
}

func TestRunExercisesResidentC5WithExactConservation(t *testing.T) {
	config := Config{
		Duration:       450 * time.Millisecond,
		CycleInterval:  100 * time.Millisecond,
		SampleInterval: 125 * time.Millisecond,
		RequestCeiling: 1_000,
		URLsPerSitemap: 64,
		HandlerDelay:   time.Millisecond,
		SampleLateness: 100 * time.Millisecond,
		CycleLateness:  80 * time.Millisecond,
	}
	snapshots := make([]Snapshot, 0)
	report := run(context.Background(), time.Now(), config, testCommit, testIdentity(), func(snapshot Snapshot) error {
		snapshots = append(snapshots, snapshot)
		return nil
	}, fixedResources)
	if report.Status != "succeeded" || report.ErrorKind != "" {
		t.Fatalf("report failed: status=%q error=%q report=%+v", report.Status, report.ErrorKind, report)
	}
	if report.CyclesCompleted != 6 {
		t.Fatalf("cycles = %d, want warmup + 5", report.CyclesCompleted)
	}
	wantTerminal := report.CyclesCompleted * jobsPerWave
	if report.Worker.Accepted != wantTerminal || report.Worker.Completed != wantTerminal || report.Results.Terminal != wantTerminal || report.Results.Succeeded != wantTerminal {
		t.Fatalf("non-conserving totals: worker=%+v results=%+v", report.Worker, report.Results)
	}
	if report.Results.Requests != wantTerminal || report.Results.WireAttempts != wantTerminal || report.Fixture.HandledRequests != wantTerminal {
		t.Fatalf("request totals do not conserve: results=%+v fixture=%+v", report.Results, report.Fixture)
	}
	if report.Worker.MaxInFlight != workerCount || report.Fixture.MaxConcurrent != workerCount || report.Fixture.MaxPerOrigin != perOriginConcurrent || report.Fixture.MinHandledPerOrigin != report.CyclesCompleted*jobsPerOrigin || report.Fixture.MaxHandledPerOrigin != report.CyclesCompleted*jobsPerOrigin {
		t.Fatalf("concurrency was not exact: worker=%+v fixture=%+v", report.Worker, report.Fixture)
	}
	if report.Connections.Open != 0 || report.Connections.InUsePermits != 0 || report.Connections.Waiters != 0 || report.Fixture.NewConnections >= report.Fixture.HandledRequests || report.Fixture.NewConnections != report.Fixture.ClosedConnections || report.Fixture.CurrentConnections != 0 {
		t.Fatalf("connections did not close: %+v", report.Connections)
	}
	if got, want := uint64(len(snapshots)), samplesFromDuration(config.Duration, config.SampleInterval); got != want || report.SamplesEmitted != want {
		t.Fatalf("samples = %d report=%d, want %d", got, report.SamplesEmitted, want)
	}
	for index, snapshot := range snapshots {
		if snapshot.Sequence != uint64(index) {
			t.Fatalf("snapshot %d sequence = %d", index, snapshot.Sequence)
		}
	}
	if snapshots[0].Phase != "baseline" || snapshots[len(snapshots)-1].Phase != "final_active" {
		t.Fatalf("unexpected phases: first=%q last=%q", snapshots[0].Phase, snapshots[len(snapshots)-1].Phase)
	}

	encoded, err := json.Marshal(struct {
		Snapshots []Snapshot `json:"snapshots"`
		Report    Report     `json:"report"`
	}{Snapshots: snapshots, Report: report})
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"fixture.invalid/job", "127.0.0.1", "sitemap.xml"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatalf("sanitized evidence leaked %q", forbidden)
		}
	}
}

func TestDefaultFourHourConfigFitsFrozenRequestCeiling(t *testing.T) {
	config := DefaultConfig(4 * time.Hour)
	if err := validateConfig(config); err != nil {
		t.Fatalf("default 4h config rejected: %v", err)
	}
	agingCycles := uint64((config.Duration + config.CycleInterval - 1) / config.CycleInterval)
	if requests := (agingCycles + 1) * jobsPerWave; requests > config.RequestCeiling {
		t.Fatalf("requests = %d, ceiling = %d", requests, config.RequestCeiling)
	}
}

func TestExactDurationBoundaryDoesNotAdmitExtraWave(t *testing.T) {
	config := Config{
		Duration:       400 * time.Millisecond,
		CycleInterval:  100 * time.Millisecond,
		SampleInterval: 150 * time.Millisecond,
		RequestCeiling: 5 * jobsPerWave,
		URLsPerSitemap: 8,
		HandlerDelay:   time.Millisecond,
		SampleLateness: 100 * time.Millisecond,
		CycleLateness:  80 * time.Millisecond,
	}
	report := run(context.Background(), time.Now(), config, testCommit, testIdentity(), func(Snapshot) error { return nil }, fixedResources)
	if report.Status != "succeeded" {
		t.Fatalf("exact-boundary run failed: %+v", report)
	}
	if report.CyclesCompleted != 5 || report.ExpectedCycles != 5 || report.Results.Terminal != 5*jobsPerWave {
		t.Fatalf("extra or missing boundary wave: cycles=%d expected=%d terminal=%d", report.CyclesCompleted, report.ExpectedCycles, report.Results.Terminal)
	}
	if report.AgingElapsedMillis < config.Duration.Milliseconds() {
		t.Fatalf("aging elapsed = %dms, want at least %dms", report.AgingElapsedMillis, config.Duration.Milliseconds())
	}
}

func TestCancellationStopsAndDrainsWithinBound(t *testing.T) {
	config := Config{
		Duration:       time.Second,
		CycleInterval:  20 * time.Millisecond,
		SampleInterval: 30 * time.Millisecond,
		RequestCeiling: 2_000,
		URLsPerSitemap: 64,
		HandlerDelay:   5 * time.Millisecond,
		SampleLateness: 100 * time.Millisecond,
		CycleLateness:  10 * time.Millisecond,
	}
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(5*time.Millisecond, cancel)
	started := time.Now()
	report := run(ctx, started, config, testCommit, testIdentity(), func(Snapshot) error { return nil }, fixedResources)
	if time.Since(started) > time.Second {
		t.Fatalf("cancellation did not drain within bound: %s", time.Since(started))
	}
	if report.Status != "failed" || report.ErrorKind != "canceled" {
		t.Fatalf("cancellation report = %+v", report)
	}
}

func TestInvalidConfigAndIdentityFailBeforeSnapshot(t *testing.T) {
	config := DefaultConfig(4 * time.Hour)
	called := false
	sink := func(Snapshot) error {
		called = true
		return nil
	}
	if report := Run(context.Background(), time.Now(), config, testCommit, testIdentity()+"-mutable", sink); report.ErrorKind != "identity_or_sink_invalid" {
		t.Fatalf("identity failure = %+v", report)
	}
	config.RequestCeiling = 1
	if report := Run(context.Background(), time.Now(), config, testCommit, testIdentity(), sink); report.ErrorKind != "config_invalid" {
		t.Fatalf("config failure = %+v", report)
	}
	if called {
		t.Fatal("preflight failure emitted a snapshot")
	}
}

func TestSinkFailureDrainsAndFailsClosed(t *testing.T) {
	config := Config{
		Duration:       50 * time.Millisecond,
		CycleInterval:  10 * time.Millisecond,
		SampleInterval: 20 * time.Millisecond,
		RequestCeiling: 1_000,
		URLsPerSitemap: 8,
		HandlerDelay:   time.Millisecond,
		SampleLateness: 100 * time.Millisecond,
		CycleLateness:  5 * time.Millisecond,
	}
	report := Run(context.Background(), time.Now(), config, testCommit, testIdentity(), func(Snapshot) error {
		return errors.New("do not serialize this raw sink error")
	})
	if report.Status != "failed" || report.ErrorKind != "snapshot_write" {
		t.Fatalf("sink failure = %+v", report)
	}
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "do not serialize") {
		t.Fatalf("raw error leaked: %s", encoded)
	}
}

func TestFixtureIsFixedLoopbackAndOriginsAreUnique(t *testing.T) {
	probe, err := startFixture(4, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer probe.close()
	if err := validateFixtureJobs(probe.jobs); err != nil {
		t.Fatal(err)
	}
	if len(probe.jobs) != originCount || len(probe.payloadHash) != 64 || len(probe.urlDigest) != 64 {
		t.Fatalf("unexpected fixture: jobs=%d payload=%q urls=%q", len(probe.jobs), probe.payloadHash, probe.urlDigest)
	}
	probe.jobs[1].Sitemap.SitemapURL = probe.jobs[0].Sitemap.SitemapURL
	if err := validateFixtureJobs(probe.jobs); err == nil {
		t.Fatal("duplicate origin unexpectedly accepted")
	}
}

func TestRetentionThresholdsAreFrozen(t *testing.T) {
	if exceedsUintRetention(100, 150, 10) {
		t.Fatal("relative boundary should be accepted")
	}
	if !exceedsUintRetention(100, 151, 10) {
		t.Fatal("relative overflow should fail")
	}
	if exceedsIntRetention(100, 150, 10) {
		t.Fatal("relative boundary should be accepted")
	}
	if !exceedsIntRetention(100, 151, 10) || !exceedsIntRetention(-1, 1, 10) {
		t.Fatal("invalid or excessive integer retention was accepted")
	}
}

func TestCgroupEvidenceRequiresObservedMemoryLimitAndEvents(t *testing.T) {
	positive := int64(1)
	limit := containerMemory
	zero := int64(0)
	resources := ResourceReport{
		CgroupMemoryCurrent: &positive,
		CgroupMemoryPeak:    &positive,
		CgroupMemoryLimit:   &limit,
		CgroupOOMEvents:     &zero,
		CgroupOOMKillEvents: &zero,
	}
	if !validCgroup(resources) {
		t.Fatal("complete cgroup evidence rejected")
	}
	resources.CgroupMemoryLimit = nil
	if validCgroup(resources) {
		t.Fatal("missing memory.max unexpectedly accepted")
	}
	resources.CgroupMemoryLimit = &limit
	report := Report{Startup: resources, Baseline: resources, FinalActive: resources, FinalClosed: resources}
	if !validCgroupEvidence(report) {
		t.Fatal("complete cgroup report rejected")
	}
	wrongLimit := containerMemory + 1
	report.FinalClosed.CgroupMemoryLimit = &wrongLimit
	if validCgroupEvidence(report) {
		t.Fatal("mismatched memory.max unexpectedly accepted")
	}
	report.FinalClosed.CgroupMemoryLimit = &limit
	report.OOMEventDelta = 1
	if validCgroupEvidence(report) {
		t.Fatal("nonzero startup-to-cleanup OOM delta unexpectedly accepted")
	}
}

func TestSourceIdentityIsExact(t *testing.T) {
	if !ValidSourceIdentity(testCommit, testIdentity()) {
		t.Fatal("exact identity rejected")
	}
	if ValidSourceIdentity(strings.ToUpper(testCommit), testIdentity()) || ValidSourceIdentity(testCommit, testIdentity()+"-latest") {
		t.Fatal("mutable or noncanonical identity accepted")
	}
}

func fixedResources() ResourceReport {
	current := int64(32 << 20)
	peak := int64(64 << 20)
	limit := containerMemory
	zero := int64(0)
	return ResourceReport{
		ProcessRSSBytes:     current,
		ProcessMaxRSSBytes:  peak,
		OpenFDs:             16,
		Goroutines:          24,
		HeapAllocBytes:      4 << 20,
		HeapInuseBytes:      8 << 20,
		HeapSysBytes:        12 << 20,
		StackInuseBytes:     2 << 20,
		RuntimeSysBytes:     16 << 20,
		GCCount:             2,
		GCPauseTotalNS:      1,
		CgroupMemoryCurrent: &current,
		CgroupMemoryPeak:    &peak,
		CgroupMemoryLimit:   &limit,
		CgroupOOMEvents:     &zero,
		CgroupOOMKillEvents: &zero,
	}
}
