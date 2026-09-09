package benchmark

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const testCommit = "0123456789abcdef0123456789abcdef01234567"

func loadTestManifest(t *testing.T) (Manifest, []byte) {
	t.Helper()
	contents, err := os.ReadFile("fleet.json")
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := DecodeManifest(contents)
	if err != nil {
		t.Fatal(err)
	}
	return manifest, contents
}

func TestFleetManifestIsEmbeddedExactOrderedAndBounded(t *testing.T) {
	manifest, contents := loadTestManifest(t)
	if len(manifest.Jobs) != 32 {
		t.Fatalf("jobs=%d, want 32", len(manifest.Jobs))
	}
	loaded, digest, err := LoadManifest("fleet.json")
	if err != nil || len(loaded.Jobs) != len(manifest.Jobs) || len(digest) != 64 {
		t.Fatalf("LoadManifest()=(%d,%q,%v)", len(loaded.Jobs), digest, err)
	}

	var raw map[string]any
	if err := json.Unmarshal(contents, &raw); err != nil {
		t.Fatal(err)
	}
	jobs := raw["jobs"].([]any)
	jobs[0], jobs[1] = jobs[1], jobs[0]
	modified, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := DecodeManifest(modified); err == nil {
		t.Fatal("reordered embedded fleet unexpectedly accepted")
	}
}

func TestFleetManifestRejectsModifiedURLRegexAndTrailingObject(t *testing.T) {
	_, contents := loadTestManifest(t)
	var raw map[string]any
	if err := json.Unmarshal(contents, &raw); err != nil {
		t.Fatal(err)
	}
	jobs := raw["jobs"].([]any)
	first := jobs[0].(map[string]any)

	originalURL := first["sitemap_url"]
	first["sitemap_url"] = "https://example.invalid/sitemap.xml"
	modifiedURL, _ := json.Marshal(raw)
	if _, err := DecodeManifest(modifiedURL); err == nil {
		t.Fatal("modified target unexpectedly accepted")
	}
	first["sitemap_url"] = originalURL

	originalFilter := first["include_literal"]
	first["include_literal"] = `^/jobs/.+$`
	modifiedFilter, _ := json.Marshal(raw)
	if _, err := DecodeManifest(modifiedFilter); err == nil {
		t.Fatal("regex filter unexpectedly accepted")
	}
	first["include_literal"] = originalFilter

	if _, err := DecodeManifest(append(contents, []byte(" {}")...)); err == nil {
		t.Fatal("trailing object unexpectedly accepted")
	}
}

func TestProfilesAndSourceIdentityAreFixed(t *testing.T) {
	for profile, want := range map[string]int{"c2": 2, "c4": 4, "c5": 5, "c8": 8, "c12": 12, "c16": 16} {
		got, ok := ConcurrencyForProfile(profile)
		if !ok || got != want {
			t.Fatalf("profile %s=(%d,%v), want %d", profile, got, ok, want)
		}
	}
	if _, ok := ConcurrencyForProfile("c32"); ok {
		t.Fatal("unbounded profile accepted")
	}
	identity := "ghcr.io/colophon-group/jobseek-sitemap-fleet-go:sha-" + testCommit
	if !ValidSourceIdentity(testCommit, identity) || ValidSourceIdentity(testCommit, identity+"-mutable") {
		t.Fatal("source identity contract violated")
	}
}

func TestCanonicalURLHashMatchesCrossLanguageVector(t *testing.T) {
	urls := []string{"https://example.test/b", "https://example.test/a"}
	const want = "7cffdea8633f0f039710fea2eb66861eaa858210ea28221f0865bafd469e4bd4"
	if got := canonicalURLSHA256(urls); got != want {
		t.Fatalf("canonicalURLSHA256()=%q, want %q", got, want)
	}
}

func TestRoundOrderIsCommittedAndWarmIsReversed(t *testing.T) {
	manifest, _ := loadTestManifest(t)
	cold := orderedJobs(manifest, 1)
	warm := orderedJobs(manifest, 2)
	if cold[0] != manifest.Jobs[0] || warm[0] != manifest.Jobs[len(manifest.Jobs)-1] {
		t.Fatal("unexpected deterministic round order")
	}
	if manifest.Jobs[0] == warm[0] || manifest.Jobs[0] != cold[0] {
		t.Fatal("orderedJobs mutated the embedded manifest")
	}
}

func TestTwoRoundsReuseOneBoundedWorkerPool(t *testing.T) {
	manifest, _ := loadTestManifest(t)
	var mu sync.Mutex
	seen := make(map[string]int)
	totalCalls := 0
	firstWave := [2]int{}
	gates := [2]chan struct{}{make(chan struct{}), make(chan struct{})}
	processor := worker.ProcessorFunc(func(_ context.Context, job worker.Job) (sitemap.Result, error) {
		mu.Lock()
		seen[job.ID]++
		call := totalCalls
		totalCalls++
		round := call / len(manifest.Jobs)
		position := call % len(manifest.Jobs)
		if position < 5 {
			firstWave[round]++
			if firstWave[round] == 5 {
				close(gates[round])
			}
		}
		mu.Unlock()
		if position < 5 {
			<-gates[round]
		}
		return sitemap.Result{
			URLs: []string{"https://result.invalid/job"},
			TransportMetrics: boundedhttp.Stats{
				Requests:     1,
				WireAttempts: 1,
				DecodedBytes: 64,
			},
		}, nil
	})
	pool, err := worker.NewWithProcessor(worker.Config{
		Workers:              5,
		Capacity:             len(manifest.Jobs),
		ResultCapacity:       len(manifest.Jobs),
		PerOriginConcurrency: 1,
		JobTimeout:           time.Second,
		ShutdownGrace:        time.Second,
	}, processor)
	if err != nil {
		t.Fatal(err)
	}
	for round, state := range []string{"cold", "pool_warm"} {
		report := runRound(context.Background(), pool, manifest, round+1, state, 5)
		if report.Status != "succeeded" || len(report.Jobs) != len(manifest.Jobs) || report.RequestCount != len(manifest.Jobs) || report.WireAttemptCount != len(manifest.Jobs) || report.MaxInFlight != 5 {
			t.Fatalf("round %d report=%+v", round+1, report)
		}
	}
	pool.Close()
	for range pool.Results() {
		t.Fatal("unexpected terminal result after both rounds drained")
	}
	stats := pool.Stats()
	if stats.Accepted != uint64(2*len(manifest.Jobs)) || stats.Completed != stats.Accepted || stats.InFlight != 0 || stats.Queued != 0 {
		t.Fatalf("final stats=%+v", stats)
	}
	for _, job := range manifest.Jobs {
		if seen[job.ID] != 2 {
			t.Fatalf("%s executions=%d, want 2", job.ID, seen[job.ID])
		}
	}
}

func TestJobFailureSerializationDoesNotLeakURLsOrErrorText(t *testing.T) {
	report := makeJobReport(worker.Result{
		JobID: "safe-id",
		Err:   errors.New("https://secret.invalid/path?token=credential"),
	})
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "secret.invalid") || strings.Contains(string(encoded), "credential") {
		t.Fatalf("sanitized report leaked: %s", encoded)
	}
	if report.ErrorKind != "internal" || report.Status != "failed" {
		t.Fatalf("failure report=%+v", report)
	}
}

func TestMaximumOverlapUsesHalfOpenIntervals(t *testing.T) {
	base := time.Unix(1, 0)
	results := []worker.Result{
		{StartedAt: base, FinishedAt: base.Add(10 * time.Millisecond)},
		{StartedAt: base.Add(5 * time.Millisecond), FinishedAt: base.Add(15 * time.Millisecond)},
		{StartedAt: base.Add(10 * time.Millisecond), FinishedAt: base.Add(20 * time.Millisecond)},
		{StartedAt: base.Add(30 * time.Millisecond), FinishedAt: base.Add(29 * time.Millisecond)},
	}
	if got := maximumOverlap(results); got != 2 {
		t.Fatalf("maximumOverlap=%d, want 2", got)
	}
}

func TestResourceMetricsMustBePresentBoundedAndLeakFree(t *testing.T) {
	peak, current, oom := int64(64<<20), int64(32<<20), int64(0)
	report := Report{
		StartupRSSBytes:          16 << 20,
		StartupOpenFDs:           4,
		ProcessMaxRSSBytes:       64 << 20,
		CgroupMemoryPeakBytes:    &peak,
		CgroupMemoryCurrentBytes: &current,
		CgroupOOMEvents:          &oom,
		Rounds: []RoundReport{
			{ProcessMaxRSSBytes: 48 << 20, ProcessCurrentRSSBytes: 24 << 20, OpenFDs: 4},
			{ProcessMaxRSSBytes: 64 << 20, ProcessCurrentRSSBytes: 30 << 20, OpenFDs: 4},
		},
		Final: FinalReport{ProcessCurrentRSSBytes: 20 << 20, OpenFDs: 4},
	}
	if !report.resourceMetricsValid() {
		t.Fatal("valid resource metrics rejected")
	}
	report.Final.OpenFDs = 5
	if report.resourceMetricsValid() {
		t.Fatal("FD leak unexpectedly accepted")
	}
	report.Final.OpenFDs = 4
	*report.CgroupOOMEvents = 1
	if report.resourceMetricsValid() {
		t.Fatal("cgroup OOM unexpectedly accepted")
	}
}

func TestCompletedRunCapturesBeforeTerminalValidation(t *testing.T) {
	order := make([]string, 0, 2)
	report := finishCompletedRun(func() Report {
		order = append(order, "capture")
		return Report{ProcessMaxRSSBytes: 64 << 20, Status: "failed"}
	}, func(report Report) bool {
		order = append(order, "validate")
		return report.ProcessMaxRSSBytes > 0
	})
	if report.Status != "succeeded" || report.ProcessMaxRSSBytes != 64<<20 {
		t.Fatalf("completed report=%+v", report)
	}
	if got := strings.Join(order, ","); got != "capture,validate" {
		t.Fatalf("terminal order=%q, want capture,validate", got)
	}
}
