// Package benchmark runs the credential-free HTTP sitemap pilot against a
// fixed multi-origin fleet. It is evidence tooling, never queue authority.
package benchmark

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const (
	SchemaVersion       = 1
	MaxManifestBytes    = 128 << 10
	MinManifestJobs     = 16
	MaxManifestJobs     = 32
	maxProtocolURLs     = 50_000
	maxResponseBytes    = 8 << 20
	maxAggregateBytes   = 32 << 20
	maxRequestsPerJob   = 1
	perOriginConcurrent = 1
	maxConnections      = 20
	maxKeepalive        = 10
	requestTimeout      = 20 * time.Second
	jobTimeout          = 25 * time.Second
	roundTimeout        = 90 * time.Second
	shutdownGrace       = 30 * time.Second
	idleConnectionTTL   = 30 * time.Second
	roundsPerProcess    = 2
)

var allowedProfiles = map[string]int{
	"c2":  2,
	"c4":  4,
	"c5":  5,
	"c8":  8,
	"c12": 12,
	"c16": 16,
}

type Manifest struct {
	SchemaVersion int           `json:"schema_version"`
	Jobs          []ManifestJob `json:"jobs"`
}

type ManifestJob struct {
	ID               string `json:"id"`
	BoardURL         string `json:"board_url"`
	SitemapURL       string `json:"sitemap_url"`
	IncludeLiteral   string `json:"include_literal"`
	ExcludeLiteral   string `json:"exclude_literal"`
	MaxURLs          int    `json:"max_urls"`
	MaxIndexChildren int    `json:"max_index_children"`
}

type ConfigReport struct {
	MaxConnections       int    `json:"max_connections"`
	MaxKeepalive         int    `json:"max_keepalive_connections"`
	PerOriginConcurrency int    `json:"per_origin_concurrency"`
	HTTPVersion          string `json:"http_version"`
	AcceptEncoding       string `json:"accept_encoding"`
	RequestTimeoutMS     int64  `json:"request_timeout_ms"`
	JobTimeoutMS         int64  `json:"job_timeout_ms"`
	MaxResponseBytes     int64  `json:"max_response_bytes"`
	MaxAggregateBytes    int64  `json:"max_aggregate_bytes"`
	MaxRequestsPerJob    int    `json:"max_requests_per_job"`
}

type Report struct {
	SchemaVersion            int           `json:"schema_version"`
	Implementation           string        `json:"implementation"`
	RuntimeVersion           string        `json:"runtime_version"`
	Profile                  string        `json:"profile"`
	Concurrency              int           `json:"concurrency"`
	SourceCommit             string        `json:"source_commit"`
	ImageIdentity            string        `json:"image_identity"`
	ManifestSHA256           string        `json:"manifest_sha256,omitempty"`
	Status                   string        `json:"status"`
	ErrorKind                string        `json:"error_kind,omitempty"`
	StartupDurationMillis    int64         `json:"startup_duration_ms"`
	StartupCPUUserMillis     int64         `json:"startup_cpu_user_ms"`
	StartupCPUSystemMillis   int64         `json:"startup_cpu_system_ms"`
	StartupRSSBytes          int64         `json:"startup_rss_bytes"`
	StartupOpenFDs           int           `json:"startup_open_fds"`
	RunDurationMillis        int64         `json:"run_duration_ms"`
	CPUUserMillis            int64         `json:"cpu_user_ms"`
	CPUSystemMillis          int64         `json:"cpu_system_ms"`
	ProcessMaxRSSBytes       int64         `json:"process_max_rss_bytes"`
	CgroupMemoryPeakBytes    *int64        `json:"cgroup_memory_peak_bytes"`
	CgroupMemoryCurrentBytes *int64        `json:"cgroup_memory_current_bytes"`
	CgroupOOMEvents          *int64        `json:"cgroup_oom_events"`
	Configured               ConfigReport  `json:"configured"`
	Rounds                   []RoundReport `json:"rounds"`
	Final                    FinalReport   `json:"final"`
}

type RoundReport struct {
	Round                  int         `json:"round"`
	ConnectionState        string      `json:"connection_state"`
	Status                 string      `json:"status"`
	ErrorKind              string      `json:"error_kind,omitempty"`
	RunDurationMillis      int64       `json:"run_duration_ms"`
	CPUUserMillis          int64       `json:"cpu_user_ms"`
	CPUSystemMillis        int64       `json:"cpu_system_ms"`
	ProcessMaxRSSBytes     int64       `json:"process_max_rss_bytes"`
	ProcessCurrentRSSBytes int64       `json:"process_current_rss_bytes"`
	OpenFDs                int         `json:"open_fds"`
	MaxInFlight            int64       `json:"max_in_flight"`
	RequestCount           int         `json:"request_count"`
	WireAttemptCount       int         `json:"wire_attempt_count"`
	DecodedBytes           int64       `json:"decoded_bytes"`
	StatusBodyBytes        int64       `json:"status_body_bytes"`
	Jobs                   []JobReport `json:"jobs"`
}

type JobReport struct {
	ID                        string `json:"id"`
	Status                    string `json:"status"`
	ErrorKind                 string `json:"error_kind,omitempty"`
	CanonicalURLCount         int    `json:"canonical_url_count"`
	CanonicalURLSHA256        string `json:"canonical_url_sha256,omitempty"`
	CanonicalURLHashAlgorithm string `json:"canonical_url_hash_algorithm,omitempty"`
	FilteredCount             int    `json:"filtered_count"`
	Truncated                 bool   `json:"truncated"`
	RequestCount              int    `json:"request_count"`
	WireAttemptCount          int    `json:"wire_attempt_count"`
	DecodedBytes              int64  `json:"decoded_bytes"`
	StatusBodyBytes           int64  `json:"status_body_bytes"`
	QueueDurationMillis       int64  `json:"queue_duration_ms"`
	ServiceDurationMillis     int64  `json:"service_duration_ms"`
}

type FinalReport struct {
	Accepted               uint64 `json:"accepted"`
	Completed              uint64 `json:"completed"`
	Failed                 int    `json:"failed"`
	Queued                 int64  `json:"queued"`
	InFlight               int64  `json:"in_flight"`
	MaxInFlight            int64  `json:"max_in_flight"`
	ConnectionsOpen        int64  `json:"connections_open"`
	ConnectionLimit        int64  `json:"connection_limit"`
	PerOriginLimit         int    `json:"per_origin_limit"`
	ConnectionWaiters      int64  `json:"connection_waiters"`
	MaximumConnections     int64  `json:"maximum_connections"`
	ProcessCurrentRSSBytes int64  `json:"process_current_rss_bytes"`
	OpenFDs                int    `json:"open_fds"`
}

type usage struct {
	user   time.Duration
	system time.Duration
	maxRSS int64
}

//go:embed fleet.json
var embeddedFleet []byte

var (
	exactFleetJobs  = map[string]ManifestJob{}
	exactFleetOrder []ManifestJob
)

var errManifestInvalid = errors.New("invalid fleet benchmark manifest")

func init() {
	var manifest Manifest
	if err := json.Unmarshal(embeddedFleet, &manifest); err != nil || manifest.SchemaVersion != SchemaVersion || len(manifest.Jobs) < MinManifestJobs || len(manifest.Jobs) > MaxManifestJobs {
		panic("invalid embedded fleet benchmark manifest")
	}
	exactFleetOrder = append([]ManifestJob(nil), manifest.Jobs...)
	for _, job := range manifest.Jobs {
		if _, exists := exactFleetJobs[job.ID]; exists {
			panic("duplicate embedded fleet benchmark job")
		}
		exactFleetJobs[job.ID] = job
	}
}

func LoadManifest(path string) (Manifest, string, error) {
	file, err := os.Open(path)
	if err != nil {
		return Manifest{}, "", errManifestInvalid
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > MaxManifestBytes {
		return Manifest{}, "", errManifestInvalid
	}
	contents, err := io.ReadAll(io.LimitReader(file, MaxManifestBytes+1))
	if err != nil || len(contents) == 0 || len(contents) > MaxManifestBytes {
		return Manifest{}, "", errManifestInvalid
	}
	manifest, err := DecodeManifest(contents)
	if err != nil {
		return Manifest{}, "", err
	}
	digest := sha256.Sum256(contents)
	return manifest, hex.EncodeToString(digest[:]), nil
}

func DecodeManifest(contents []byte) (Manifest, error) {
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	var manifest Manifest
	if err := decoder.Decode(&manifest); err != nil {
		return Manifest{}, errManifestInvalid
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return Manifest{}, errManifestInvalid
	}
	if err := validateManifest(manifest); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

func validateManifest(manifest Manifest) error {
	if manifest.SchemaVersion != SchemaVersion || len(manifest.Jobs) < MinManifestJobs || len(manifest.Jobs) > MaxManifestJobs {
		return errManifestInvalid
	}
	if len(exactFleetJobs) != len(manifest.Jobs) {
		return errManifestInvalid
	}
	seenIDs := make(map[string]struct{}, len(manifest.Jobs))
	seenOrigins := make(map[string]struct{}, len(manifest.Jobs))
	for index, job := range manifest.Jobs {
		expected, ok := exactFleetJobs[job.ID]
		if !ok || job != expected || job != exactFleetOrder[index] || job.MaxURLs != maxProtocolURLs || job.MaxIndexChildren != 1 {
			return errManifestInvalid
		}
		if hasRegexSyntax(job.IncludeLiteral) || hasRegexSyntax(job.ExcludeLiteral) {
			return errManifestInvalid
		}
		parsed, err := url.Parse(job.SitemapURL)
		if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
			return errManifestInvalid
		}
		board, err := url.Parse(job.BoardURL)
		if err != nil || board.Scheme != "https" || board.Host == "" || board.User != nil {
			return errManifestInvalid
		}
		origin := parsed.Scheme + "://" + strings.ToLower(parsed.Host)
		if _, exists := seenIDs[job.ID]; exists {
			return errManifestInvalid
		}
		if _, exists := seenOrigins[origin]; exists {
			return errManifestInvalid
		}
		seenIDs[job.ID] = struct{}{}
		seenOrigins[origin] = struct{}{}
	}
	return nil
}

func hasRegexSyntax(value string) bool {
	return strings.ContainsAny(value, `\.^$*+?{}[]|()`)
}

func ValidSourceIdentity(sourceCommit, imageIdentity string) bool {
	if len(sourceCommit) != 40 {
		return false
	}
	for _, value := range []byte(sourceCommit) {
		if !('0' <= value && value <= '9') && !('a' <= value && value <= 'f') {
			return false
		}
	}
	return imageIdentity == "ghcr.io/colophon-group/jobseek-sitemap-fleet-go:sha-"+sourceCommit
}

func ConcurrencyForProfile(profile string) (int, bool) {
	concurrency, ok := allowedProfiles[profile]
	return concurrency, ok
}

func Run(ctx context.Context, started time.Time, manifest Manifest, manifestSHA256, profile, sourceCommit, imageIdentity string) Report {
	if started.IsZero() {
		started = time.Now()
	}
	concurrency, profileOK := ConcurrencyForProfile(profile)
	report := Report{
		SchemaVersion:  SchemaVersion,
		Implementation: "go",
		RuntimeVersion: runtime.Version(),
		Profile:        profile,
		Concurrency:    concurrency,
		SourceCommit:   sourceCommit,
		ImageIdentity:  imageIdentity,
		ManifestSHA256: manifestSHA256,
		Status:         "failed",
		Rounds:         make([]RoundReport, 0, roundsPerProcess),
		Configured: ConfigReport{
			MaxConnections:       maxConnections,
			MaxKeepalive:         maxKeepalive,
			PerOriginConcurrency: perOriginConcurrent,
			HTTPVersion:          "1.1",
			AcceptEncoding:       "identity",
			RequestTimeoutMS:     requestTimeout.Milliseconds(),
			JobTimeoutMS:         jobTimeout.Milliseconds(),
			MaxResponseBytes:     maxResponseBytes,
			MaxAggregateBytes:    maxAggregateBytes,
			MaxRequestsPerJob:    maxRequestsPerJob,
		},
	}
	finish := func() Report {
		after := readUsage()
		report.RunDurationMillis = time.Since(started).Milliseconds()
		report.CPUUserMillis = after.user.Milliseconds()
		report.CPUSystemMillis = after.system.Milliseconds()
		report.ProcessMaxRSSBytes = after.maxRSS
		report.CgroupMemoryPeakBytes = readCgroupMemoryPeak()
		report.CgroupMemoryCurrentBytes = readCgroupValue("memory.current")
		report.CgroupOOMEvents = readCgroupEvent("oom")
		return report
	}
	if ctx == nil || !profileOK || !ValidSourceIdentity(sourceCommit, imageIdentity) {
		report.ErrorKind = "identity_or_profile_invalid"
		return finish()
	}
	if err := validateManifest(manifest); err != nil {
		report.ErrorKind = "manifest_invalid"
		return finish()
	}

	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           requestTimeout,
		MaxDecodedBodyBytes:      maxResponseBytes,
		MaxRequests:              maxRequestsPerJob,
		MaxAggregateDecodedBytes: maxAggregateBytes,
		RequireIdentityEncoding:  true,
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          maxKeepalive,
			MaxIdleConnsPerHost:   perOriginConcurrent,
			MaxConnsPerHost:       perOriginConcurrent,
			MaxConnections:        maxConnections,
			MaxConcurrentRequests: concurrency,
			IdleConnTimeout:       idleConnectionTTL,
		},
	})
	if err != nil {
		report.ErrorKind = "client_config"
		return finish()
	}
	defer client.Close()

	pool, err := worker.New(client, worker.Config{
		Workers:              concurrency,
		Capacity:             len(manifest.Jobs),
		ResultCapacity:       len(manifest.Jobs),
		PerOriginConcurrency: perOriginConcurrent,
		JobTimeout:           jobTimeout,
		ShutdownGrace:        shutdownGrace,
	})
	if err != nil {
		report.ErrorKind = "worker_config"
		return finish()
	}
	report.StartupDurationMillis = time.Since(started).Milliseconds()
	startupUsage := readUsage()
	report.StartupCPUUserMillis = startupUsage.user.Milliseconds()
	report.StartupCPUSystemMillis = startupUsage.system.Milliseconds()
	report.StartupRSSBytes = readCurrentRSS()
	report.StartupOpenFDs = readOpenFDs()

	failed := 0
	for round := 1; round <= roundsPerProcess; round++ {
		state := "pool_warm"
		if round == 1 {
			state = "cold"
		}
		roundReport := runRound(ctx, pool, manifest, round, state, concurrency)
		report.Rounds = append(report.Rounds, roundReport)
		if roundReport.Status != "succeeded" {
			failed++
			break
		}
	}
	pool.Close()
	for range pool.Results() {
		failed++
	}
	stats := pool.Stats()
	client.Close()
	connections := client.ConnectionStats()
	report.Final = FinalReport{
		Accepted:               stats.Accepted,
		Completed:              stats.Completed,
		Failed:                 failed,
		Queued:                 stats.Queued,
		InFlight:               stats.InFlight,
		MaxInFlight:            stats.MaxInFlight,
		ConnectionsOpen:        connections.Open,
		ConnectionLimit:        connections.PermitLimit,
		PerOriginLimit:         perOriginConcurrent,
		ConnectionWaiters:      connections.Waiters,
		MaximumConnections:     connections.MaximumOpen,
		ProcessCurrentRSSBytes: readCurrentRSS(),
		OpenFDs:                readOpenFDs(),
	}
	expected := uint64(len(manifest.Jobs) * roundsPerProcess)
	return finishCompletedRun(finish, func(report Report) bool {
		return failed == 0 && len(report.Rounds) == roundsPerProcess && stats.Accepted == expected && stats.Completed == expected && stats.Panics == 0 && stats.Queued == 0 && stats.InFlight == 0 && stats.MaxInFlight == int64(concurrency) && connections.Open == 0 && connections.InUsePermits == 0 && connections.Waiters == 0 && connections.PermitLimit == maxConnections && connections.MaximumOpen <= maxConnections && report.resourceMetricsValid()
	})
}

// finishCompletedRun captures terminal metrics before applying the final gate,
// and returns the exact captured report that the gate evaluated.
func finishCompletedRun(capture func() Report, valid func(Report) bool) Report {
	report := capture()
	if !valid(report) {
		report.ErrorKind = "final_invariant"
		return report
	}
	report.Status = "succeeded"
	return report
}

func runRound(parent context.Context, pool *worker.Pool, manifest Manifest, round int, state string, expectedConcurrency int) RoundReport {
	started := time.Now()
	beforeUsage := readUsage()
	beforeStats := pool.Stats()
	report := RoundReport{
		Round:           round,
		ConnectionState: state,
		Status:          "failed",
		Jobs:            make([]JobReport, 0, len(manifest.Jobs)),
	}
	ctx, cancel := context.WithTimeout(parent, roundTimeout)
	defer cancel()
	accepted := 0
	for _, job := range orderedJobs(manifest, round) {
		err := pool.Submit(ctx, worker.Job{
			ID:      job.ID,
			Context: ctx,
			Sitemap: sitemap.Config{
				SitemapURL:       job.SitemapURL,
				IncludeLiteral:   job.IncludeLiteral,
				ExcludeLiteral:   job.ExcludeLiteral,
				MaxURLs:          job.MaxURLs,
				MaxIndexChildren: job.MaxIndexChildren,
				RootMaxAttempts:  1,
				RequireURLSet:    true,
			},
		})
		if err != nil {
			report.ErrorKind = typedErrorKind(err)
			break
		}
		accepted++
	}

	results := make([]worker.Result, 0, accepted)
	for len(report.Jobs) < accepted {
		select {
		case result, ok := <-pool.Results():
			if !ok {
				report.ErrorKind = "results_closed"
				return finishRound(report, started, beforeUsage, beforeStats, pool.Stats())
			}
			results = append(results, result)
			report.Jobs = append(report.Jobs, makeJobReport(result))
		case <-ctx.Done():
			report.ErrorKind = "round_deadline"
			return finishRound(report, started, beforeUsage, beforeStats, pool.Stats())
		}
	}
	sort.Slice(report.Jobs, func(i, j int) bool { return report.Jobs[i].ID < report.Jobs[j].ID })
	if accepted != len(manifest.Jobs) {
		if report.ErrorKind == "" {
			report.ErrorKind = "admission_incomplete"
		}
		return finishRound(report, started, beforeUsage, beforeStats, pool.Stats())
	}
	for _, job := range report.Jobs {
		report.RequestCount += job.RequestCount
		report.WireAttemptCount += job.WireAttemptCount
		report.DecodedBytes += job.DecodedBytes
		report.StatusBodyBytes += job.StatusBodyBytes
		if job.Status != "succeeded" {
			report.ErrorKind = "job_failed"
		}
	}
	report.MaxInFlight = maximumOverlap(results)
	if report.MaxInFlight != int64(expectedConcurrency) {
		report.ErrorKind = "concurrency_not_reached"
	}
	if report.ErrorKind == "" && report.RequestCount == len(manifest.Jobs) && report.WireAttemptCount == len(manifest.Jobs) && report.StatusBodyBytes == 0 {
		report.Status = "succeeded"
	} else if report.ErrorKind == "" {
		report.ErrorKind = "request_invariant"
	}
	return finishRound(report, started, beforeUsage, beforeStats, pool.Stats())
}

func (r Report) resourceMetricsValid() bool {
	if r.StartupRSSBytes <= 0 || r.StartupOpenFDs < 3 || r.StartupOpenFDs > 256 || r.ProcessMaxRSSBytes <= 0 || r.CgroupMemoryPeakBytes == nil || *r.CgroupMemoryPeakBytes <= 0 || r.CgroupMemoryCurrentBytes == nil || *r.CgroupMemoryCurrentBytes <= 0 || r.CgroupOOMEvents == nil || *r.CgroupOOMEvents != 0 || r.Final.ProcessCurrentRSSBytes <= 0 || r.Final.OpenFDs < 3 || r.Final.OpenFDs > r.StartupOpenFDs {
		return false
	}
	for _, round := range r.Rounds {
		if round.ProcessMaxRSSBytes <= 0 || round.ProcessCurrentRSSBytes <= 0 || round.OpenFDs < 3 || round.OpenFDs > 256 {
			return false
		}
	}
	return true
}

func orderedJobs(manifest Manifest, round int) []ManifestJob {
	jobs := append([]ManifestJob(nil), manifest.Jobs...)
	if round == 2 {
		for left, right := 0, len(jobs)-1; left < right; left, right = left+1, right-1 {
			jobs[left], jobs[right] = jobs[right], jobs[left]
		}
	}
	return jobs
}

func finishRound(report RoundReport, started time.Time, beforeUsage usage, beforeStats, afterStats worker.Stats) RoundReport {
	afterUsage := readUsage()
	report.RunDurationMillis = time.Since(started).Milliseconds()
	report.CPUUserMillis = (afterUsage.user - beforeUsage.user).Milliseconds()
	report.CPUSystemMillis = (afterUsage.system - beforeUsage.system).Milliseconds()
	report.ProcessMaxRSSBytes = afterUsage.maxRSS
	report.ProcessCurrentRSSBytes = readCurrentRSS()
	report.OpenFDs = readOpenFDs()
	if afterStats.Accepted-beforeStats.Accepted != uint64(len(report.Jobs)) || afterStats.Completed-beforeStats.Completed != uint64(len(report.Jobs)) {
		if report.ErrorKind == "" {
			report.ErrorKind = "worker_invariant"
		}
		report.Status = "failed"
	}
	return report
}

func maximumOverlap(results []worker.Result) int64 {
	type event struct {
		at    time.Time
		delta int
	}
	events := make([]event, 0, 2*len(results))
	for _, result := range results {
		if result.StartedAt.IsZero() || result.FinishedAt.Before(result.StartedAt) {
			continue
		}
		events = append(events,
			event{at: result.StartedAt, delta: 1},
			event{at: result.FinishedAt, delta: -1},
		)
	}
	sort.Slice(events, func(i, j int) bool {
		if events[i].at.Equal(events[j].at) {
			return events[i].delta < events[j].delta
		}
		return events[i].at.Before(events[j].at)
	})
	current, maximum := 0, 0
	for _, event := range events {
		current += event.delta
		if current > maximum {
			maximum = current
		}
	}
	return int64(maximum)
}

func makeJobReport(result worker.Result) JobReport {
	report := JobReport{
		ID:                    result.JobID,
		Status:                "failed",
		CanonicalURLCount:     len(result.Sitemap.URLs),
		FilteredCount:         result.Sitemap.FilteredCount,
		Truncated:             result.Sitemap.Truncated,
		RequestCount:          result.Sitemap.TransportMetrics.Requests,
		WireAttemptCount:      result.Sitemap.TransportMetrics.WireAttempts,
		DecodedBytes:          result.Sitemap.TransportMetrics.DecodedBytes,
		StatusBodyBytes:       result.Sitemap.TransportMetrics.StatusBodyBytes,
		QueueDurationMillis:   result.QueueDuration.Milliseconds(),
		ServiceDurationMillis: result.ServiceDuration.Milliseconds(),
	}
	if result.Err != nil {
		report.ErrorKind = typedErrorKind(result.Err)
		return report
	}
	if report.RequestCount != 1 || report.WireAttemptCount != 1 || report.DecodedBytes <= 0 || report.DecodedBytes > maxResponseBytes || report.StatusBodyBytes != 0 {
		report.ErrorKind = "request_invariant"
		return report
	}
	report.CanonicalURLSHA256 = canonicalURLSHA256(result.Sitemap.URLs)
	report.CanonicalURLHashAlgorithm = "sha256-length-prefixed-v1"
	if len(result.Sitemap.URLs) == 0 {
		report.ErrorKind = "empty_result"
		return report
	}
	if result.Sitemap.Truncated {
		report.ErrorKind = "truncated"
		return report
	}
	report.Status = "succeeded"
	return report
}

func canonicalURLSHA256(urls []string) string {
	canonical := append([]string(nil), urls...)
	sort.Strings(canonical)
	digest := sha256.New()
	var length [8]byte
	for _, value := range canonical {
		binary.BigEndian.PutUint64(length[:], uint64(len(value)))
		_, _ = digest.Write(length[:])
		_, _ = digest.Write([]byte(value))
	}
	return hex.EncodeToString(digest.Sum(nil))
}

func typedErrorKind(err error) string {
	var panicErr *worker.PanicError
	if errors.As(err, &panicErr) {
		return "worker_panic"
	}
	var retryErr *sitemap.RetryExhaustedError
	if errors.As(err, &retryErr) {
		return "sitemap_retry_exhausted"
	}
	var sitemapErr *sitemap.Error
	if errors.As(err, &sitemapErr) {
		return "sitemap_" + string(sitemapErr.Kind)
	}
	var httpErr *boundedhttp.Error
	if errors.As(err, &httpErr) {
		return "http_" + string(httpErr.Kind)
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline"
	}
	if errors.Is(err, context.Canceled) {
		return "canceled"
	}
	return "internal"
}

func readUsage() usage {
	var value syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &value); err != nil {
		return usage{}
	}
	maxRSS := value.Maxrss
	if runtime.GOOS == "linux" {
		maxRSS *= 1024
	}
	return usage{
		user:   timevalDuration(value.Utime),
		system: timevalDuration(value.Stime),
		maxRSS: maxRSS,
	}
}

func timevalDuration(value syscall.Timeval) time.Duration {
	return time.Duration(value.Sec)*time.Second + time.Duration(value.Usec)*time.Microsecond
}

func readCgroupMemoryPeak() *int64 {
	return readCgroupValue("memory.peak")
}

func readCgroupValue(name string) *int64 {
	contents, err := os.ReadFile("/sys/fs/cgroup/" + name)
	if err != nil {
		return nil
	}
	value, err := strconv.ParseInt(strings.TrimSpace(string(contents)), 10, 64)
	if err != nil || value <= 0 {
		return nil
	}
	return &value
}

func readCgroupEvent(name string) *int64 {
	contents, err := os.ReadFile("/sys/fs/cgroup/memory.events")
	if err != nil {
		return nil
	}
	for _, line := range strings.Split(string(contents), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 || fields[0] != name {
			continue
		}
		value, err := strconv.ParseInt(fields[1], 10, 64)
		if err != nil || value < 0 {
			return nil
		}
		return &value
	}
	return nil
}

func readCurrentRSS() int64 {
	contents, err := os.ReadFile("/proc/self/status")
	if err != nil {
		return -1
	}
	for _, line := range strings.Split(string(contents), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == "VmRSS:" && fields[2] == "kB" {
			value, err := strconv.ParseInt(fields[1], 10, 64)
			if err == nil && value >= 0 {
				return value * 1024
			}
		}
	}
	return -1
}

func readOpenFDs() int {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return -1
	}
	return len(entries)
}

func FailureReport(profile, sourceCommit, imageIdentity, kind string, started time.Time) Report {
	concurrency, _ := ConcurrencyForProfile(profile)
	if kind == "" {
		kind = "internal"
	}
	return Report{
		SchemaVersion:     SchemaVersion,
		Implementation:    "go",
		RuntimeVersion:    runtime.Version(),
		Profile:           profile,
		Concurrency:       concurrency,
		SourceCommit:      sourceCommit,
		ImageIdentity:     imageIdentity,
		Status:            "failed",
		ErrorKind:         kind,
		RunDurationMillis: time.Since(started).Milliseconds(),
		Rounds:            []RoundReport{},
	}
}
