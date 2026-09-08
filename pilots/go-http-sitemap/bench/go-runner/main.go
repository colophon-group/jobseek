package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/url"
	"os"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

type inputJob struct {
	ID              string `json:"id"`
	Origin          string `json:"origin"`
	SitemapURL      string `json:"sitemap_url"`
	Scenario        string `json:"scenario"`
	ExpectedOutcome string `json:"expected_outcome"`
}

type command struct {
	Action         string     `json:"action"`
	BatchID        string     `json:"batch_id"`
	Phase          string     `json:"phase"`
	ManifestSHA256 string     `json:"manifest_sha256"`
	Jobs           []inputJob `json:"jobs"`
}

type jobOutput struct {
	ID               string `json:"id"`
	Origin           string `json:"origin"`
	Scenario         string `json:"scenario"`
	Outcome          string `json:"outcome"`
	ErrorType        string `json:"error_type,omitempty"`
	URLCount         int    `json:"url_count"`
	URLDigestSHA256  string `json:"url_digest_sha256"`
	FilteredCount    int    `json:"filtered_count"`
	Truncated        bool   `json:"truncated"`
	Requests         int    `json:"requests"`
	WireAttempts     int    `json:"wire_attempts"`
	DecodedBytes     int64  `json:"decoded_bytes"`
	StatusBodyBytes  int64  `json:"status_body_bytes"`
	QueueNS          int64  `json:"queue_ns"`
	ServiceNS        int64  `json:"service_ns"`
	EndToEndNS       int64  `json:"end_to_end_ns"`
	AcceptedUnixNano int64  `json:"accepted_ns"`
	StartedUnixNano  int64  `json:"started_ns"`
	FinishedUnixNano int64  `json:"finished_ns"`
}

type batchOutput struct {
	Type            string                      `json:"type"`
	Implementation  string                      `json:"implementation"`
	BatchID         string                      `json:"batch_id"`
	Phase           string                      `json:"phase"`
	ManifestSHA256  string                      `json:"manifest_sha256"`
	Jobs            []jobOutput                 `json:"jobs"`
	WallNS          int64                       `json:"wall_ns"`
	UserCPUNS       int64                       `json:"user_cpu_ns"`
	SysCPUNS        int64                       `json:"sys_cpu_ns"`
	PeakRSSKiB      int64                       `json:"peak_rss_kib"`
	OpenFDsEnd      int                         `json:"open_fds_end"`
	MaxQueued       int                         `json:"max_queued"`
	MaxInFlight     int                         `json:"max_in_flight"`
	MaxUnfinished   int                         `json:"max_unfinished"`
	Accepted        uint64                      `json:"accepted"`
	Completed       uint64                      `json:"completed"`
	Panics          uint64                      `json:"panics"`
	Unfinished      int                         `json:"unfinished"`
	GoroutinesEnd   int                         `json:"goroutines_end"`
	ProcessChildren int                         `json:"process_children"`
	Transport       boundedhttp.ConnectionStats `json:"transport_connections"`
}

type readyOutput struct {
	Type                     string        `json:"type"`
	Implementation           string        `json:"implementation"`
	PID                      int           `json:"pid"`
	Runtime                  string        `json:"runtime"`
	GOOS                     string        `json:"goos"`
	GOARCH                   string        `json:"goarch"`
	GOMAXPROCS               int           `json:"gomaxprocs"`
	Workers                  int           `json:"workers"`
	Capacity                 int           `json:"capacity"`
	ResultCapacity           int           `json:"result_capacity"`
	PerOriginConcurrency     int           `json:"per_origin_concurrency"`
	GlobalActiveRequests     int           `json:"global_active_requests"`
	GlobalTotalConnections   int           `json:"global_total_connections"`
	GlobalIdleConnections    int           `json:"global_idle_connections"`
	IdleConnectionsPerHost   int           `json:"idle_connections_per_origin"`
	PerOriginIdleEnforcement string        `json:"per_origin_idle_enforcement"`
	IdleExpirySeconds        int           `json:"idle_expiry_seconds"`
	RequestTimeoutSeconds    int           `json:"request_timeout_seconds"`
	JobTimeoutSeconds        int           `json:"job_timeout_seconds"`
	FileDescriptorSoftLimit  uint64        `json:"file_descriptor_soft_limit"`
	ClientBeforeReady        bool          `json:"client_constructed_before_ready"`
	PoolBeforeReady          bool          `json:"pool_constructed_before_ready"`
	ExecutableSHA256         string        `json:"executable_sha256"`
	BuildInfo                buildIdentity `json:"build_info"`
}

type buildIdentity struct {
	GoVersion string            `json:"go_version"`
	Path      string            `json:"path"`
	Module    string            `json:"module"`
	Settings  map[string]string `json:"settings"`
}

type usageSnapshot struct {
	userNS     int64
	systemNS   int64
	peakRSSKiB int64
}

func usage() usageSnapshot {
	var value syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &value); err != nil {
		panic(err)
	}
	peak := value.Maxrss
	if runtime.GOOS == "darwin" {
		peak /= 1024
	}
	return usageSnapshot{
		userNS:     value.Utime.Sec*1_000_000_000 + int64(value.Utime.Usec)*1_000,
		systemNS:   value.Stime.Sec*1_000_000_000 + int64(value.Stime.Usec)*1_000,
		peakRSSKiB: peak,
	}
}

func setFileDescriptorLimit(limit uint64) uint64 {
	var current syscall.Rlimit
	if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &current); err != nil {
		panic(err)
	}
	if limit == 0 || limit > current.Max {
		panic("invalid file-descriptor limit")
	}
	current.Cur = limit
	if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &current); err != nil {
		panic(err)
	}
	return current.Cur
}

func openFDs() int {
	for _, directory := range []string{"/proc/self/fd", "/dev/fd"} {
		entries, err := os.ReadDir(directory)
		if err == nil {
			return len(entries)
		}
	}
	return -1
}

func ownBuildIdentity() (string, buildIdentity, error) {
	executable, err := os.Executable()
	if err != nil {
		return "", buildIdentity{}, err
	}
	data, err := os.ReadFile(executable)
	if err != nil {
		return "", buildIdentity{}, err
	}
	digest := sha256.Sum256(data)
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", buildIdentity{}, errors.New("Go build information is unavailable")
	}
	settings := make(map[string]string, len(info.Settings))
	for _, setting := range info.Settings {
		settings[setting.Key] = setting.Value
	}
	return hex.EncodeToString(digest[:]), buildIdentity{
		GoVersion: info.GoVersion,
		Path:      info.Path,
		Module:    info.Main.Path,
		Settings:  settings,
	}, nil
}

func manifestDigest(jobs []inputJob) (string, error) {
	hash := sha256.New()
	for _, job := range jobs {
		fields := []string{job.ID, job.Origin, job.SitemapURL, job.Scenario, job.ExpectedOutcome}
		for _, field := range fields {
			if strings.ContainsAny(field, "\t\r\n") {
				return "", errors.New("job manifest contains a forbidden separator")
			}
		}
		_, _ = fmt.Fprintf(hash, "%s\t%s\t%s\t%s\t%s\n", fields[0], fields[1], fields[2], fields[3], fields[4])
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func validateJob(input inputJob) error {
	if input.ID == "" || input.Origin == "" || input.SitemapURL == "" || input.Scenario == "" {
		return errors.New("job fields must be non-empty")
	}
	origin, err := url.Parse(input.Origin)
	if err != nil || origin.Scheme != "http" || origin.User != nil || origin.Host == "" || (origin.Path != "" && origin.Path != "/") {
		return errors.New("invalid fixture origin")
	}
	target, err := url.Parse(input.SitemapURL)
	if err != nil || target.Scheme != origin.Scheme || target.Host != origin.Host || target.User != nil {
		return errors.New("sitemap URL escaped its declared fixture origin")
	}
	addresses, err := net.LookupIP(origin.Hostname())
	if err != nil || len(addresses) == 0 {
		return errors.New("fixture origin did not resolve")
	}
	for _, address := range addresses {
		if !address.IsLoopback() && !address.IsPrivate() {
			return errors.New("fixture origin resolved outside loopback/private address space")
		}
	}
	return nil
}

func classify(err error) (string, string) {
	if err == nil {
		return "success", ""
	}
	var retryErr *sitemap.RetryExhaustedError
	if errors.As(err, &retryErr) {
		return "retry_exhausted", "sitemap.RetryExhaustedError"
	}
	var sitemapErr *sitemap.Error
	if errors.As(err, &sitemapErr) {
		return "error", "sitemap.Error:" + string(sitemapErr.Kind)
	}
	var transportErr *boundedhttp.Error
	if errors.As(err, &transportErr) {
		return "error", "boundedhttp.Error:" + string(transportErr.Kind)
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline", "context.DeadlineExceeded"
	}
	return "error", fmt.Sprintf("%T", err)
}

func urlDigest(urls []string) string {
	ordered := append([]string(nil), urls...)
	sort.Strings(ordered)
	digest := sha256.Sum256([]byte(strings.Join(ordered, "\n")))
	return hex.EncodeToString(digest[:])
}

func intervalMax(intervals [][2]int64) int {
	type event struct {
		at    int64
		delta int
	}
	events := make([]event, 0, len(intervals)*2)
	for _, interval := range intervals {
		if interval[1] <= interval[0] {
			continue
		}
		events = append(events, event{at: interval[0], delta: 1}, event{at: interval[1], delta: -1})
	}
	sort.Slice(events, func(i, j int) bool {
		if events[i].at == events[j].at {
			return events[i].delta < events[j].delta
		}
		return events[i].at < events[j].at
	})
	current := 0
	maximum := 0
	for _, event := range events {
		current += event.delta
		if current > maximum {
			maximum = current
		}
	}
	return maximum
}

func runBatch(pool *worker.Pool, client *boundedhttp.Client, input command) batchOutput {
	digest, err := manifestDigest(input.Jobs)
	if err != nil || digest != input.ManifestSHA256 {
		panic("job manifest digest mismatch")
	}
	byWorkerID := make(map[string]inputJob, len(input.Jobs))
	for _, job := range input.Jobs {
		if err := validateJob(job); err != nil {
			panic(err)
		}
		workerID := input.BatchID + "-" + job.ID
		if _, exists := byWorkerID[workerID]; exists {
			panic("duplicate job ID")
		}
		byWorkerID[workerID] = job
	}

	statsBefore := pool.Stats()
	usageBefore := usage()
	wallStart := time.Now()
	feedErr := make(chan error, 1)
	go func() {
		for _, job := range input.Jobs {
			workerID := input.BatchID + "-" + job.ID
			err := pool.Submit(context.Background(), worker.Job{
				ID: workerID,
				Sitemap: sitemap.Config{
					SitemapURL:       job.SitemapURL,
					MaxURLs:          50_000,
					MaxIndexChildren: 4,
				},
			})
			if err != nil {
				feedErr <- err
				return
			}
		}
		feedErr <- nil
	}()

	outputs := make([]jobOutput, 0, len(input.Jobs))
	queueIntervals := make([][2]int64, 0, len(input.Jobs))
	serviceIntervals := make([][2]int64, 0, len(input.Jobs))
	unfinishedIntervals := make([][2]int64, 0, len(input.Jobs))
	for len(outputs) < len(input.Jobs) {
		result, ok := <-pool.Results()
		if !ok {
			panic("worker result stream closed during batch")
		}
		job, exists := byWorkerID[result.JobID]
		if !exists {
			panic("worker emitted a result for another batch")
		}
		delete(byWorkerID, result.JobID)
		outcome, errorType := classify(result.Err)
		acceptedUnixNano := result.AcceptedAt.UnixNano()
		startedUnixNano := result.StartedAt.UnixNano()
		finishedUnixNano := result.FinishedAt.UnixNano()
		outputs = append(outputs, jobOutput{
			ID:               job.ID,
			Origin:           job.Origin,
			Scenario:         job.Scenario,
			Outcome:          outcome,
			ErrorType:        errorType,
			URLCount:         len(result.Sitemap.URLs),
			URLDigestSHA256:  urlDigest(result.Sitemap.URLs),
			FilteredCount:    result.Sitemap.FilteredCount,
			Truncated:        result.Sitemap.Truncated,
			Requests:         result.Sitemap.TransportMetrics.Requests,
			WireAttempts:     result.Sitemap.TransportMetrics.WireAttempts,
			DecodedBytes:     result.Sitemap.TransportMetrics.DecodedBytes,
			StatusBodyBytes:  result.Sitemap.TransportMetrics.StatusBodyBytes,
			QueueNS:          startedUnixNano - acceptedUnixNano,
			ServiceNS:        finishedUnixNano - startedUnixNano,
			EndToEndNS:       finishedUnixNano - acceptedUnixNano,
			AcceptedUnixNano: acceptedUnixNano,
			StartedUnixNano:  startedUnixNano,
			FinishedUnixNano: finishedUnixNano,
		})
		queueIntervals = append(queueIntervals, [2]int64{acceptedUnixNano, startedUnixNano})
		serviceIntervals = append(serviceIntervals, [2]int64{startedUnixNano, finishedUnixNano})
		unfinishedIntervals = append(unfinishedIntervals, [2]int64{acceptedUnixNano, finishedUnixNano})
	}
	if err := <-feedErr; err != nil {
		panic(err)
	}
	completionDeadline := time.Now().Add(time.Second)
	for pool.Stats().Completed-statsBefore.Completed != uint64(len(input.Jobs)) {
		if time.Now().After(completionDeadline) {
			panic("worker completion accounting did not settle")
		}
		runtime.Gosched()
	}
	wallNS := time.Since(wallStart).Nanoseconds()
	usageAfter := usage()
	statsAfter := pool.Stats()
	transportStats := settledConnectionStats(client.ConnectionStats, time.Second)
	sort.Slice(outputs, func(i, j int) bool { return outputs[i].ID < outputs[j].ID })
	return batchOutput{
		Type:            "batch",
		Implementation:  "go-worker-pilot",
		BatchID:         input.BatchID,
		Phase:           input.Phase,
		ManifestSHA256:  digest,
		Jobs:            outputs,
		WallNS:          wallNS,
		UserCPUNS:       usageAfter.userNS - usageBefore.userNS,
		SysCPUNS:        usageAfter.systemNS - usageBefore.systemNS,
		PeakRSSKiB:      usageAfter.peakRSSKiB,
		OpenFDsEnd:      openFDs(),
		MaxQueued:       intervalMax(queueIntervals),
		MaxInFlight:     intervalMax(serviceIntervals),
		MaxUnfinished:   intervalMax(unfinishedIntervals),
		Accepted:        statsAfter.Accepted - statsBefore.Accepted,
		Completed:       statsAfter.Completed - statsBefore.Completed,
		Panics:          statsAfter.Panics - statsBefore.Panics,
		Unfinished:      len(byWorkerID),
		GoroutinesEnd:   runtime.NumGoroutine(),
		ProcessChildren: 0,
		Transport:       transportStats,
	}
}

// settledConnectionStats samples after the timed batch until independently
// loaded counters cannot straddle limitedConn's two atomic close updates. A
// persistent mismatch or waiter is a real runner invariant failure, not an
// admissible measurement.
func settledConnectionStats(
	snapshot func() boundedhttp.ConnectionStats,
	timeout time.Duration,
) boundedhttp.ConnectionStats {
	deadline := time.Now().Add(timeout)
	for {
		stats := snapshot()
		if stats.Open == stats.InUsePermits && stats.Waiters == 0 {
			return stats
		}
		if time.Now().After(deadline) {
			panic("client-local transport counters did not settle after the batch")
		}
		runtime.Gosched()
	}
}

func main() {
	workers := flag.Int("workers", 0, "fixed worker goroutines")
	capacity := flag.Int("capacity", 0, "accepted unfinished job bound")
	resultCapacity := flag.Int("result-capacity", 0, "bounded result sink")
	perOrigin := flag.Int("per-origin", 0, "per-origin job concurrency")
	globalActive := flag.Int("global-active", 20, "global active HTTP request cap")
	totalConnections := flag.Int("total-connections", 20, "global active plus idle connection cap")
	globalIdle := flag.Int("global-idle", 10, "global idle connection cap")
	idlePerOrigin := flag.Int("idle-per-origin", 2, "per-origin idle connection cap")
	idleExpiry := flag.Int("idle-expiry-seconds", 5, "idle connection expiry")
	requestTimeout := flag.Int("request-timeout-seconds", 5, "request timeout")
	jobTimeout := flag.Int("job-timeout-seconds", 15, "job deadline")
	fdLimit := flag.Uint64("fd-limit", 256, "soft file-descriptor limit")
	flag.Parse()

	softLimit := setFileDescriptorLimit(*fdLimit)
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Duration(*requestTimeout) * time.Second,
		MaxDecodedBodyBytes:      16 * 1024 * 1024,
		MaxRequests:              8,
		MaxAggregateDecodedBytes: 32 * 1024 * 1024,
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          *globalIdle,
			MaxIdleConnsPerHost:   *idlePerOrigin,
			MaxConnsPerHost:       *perOrigin,
			MaxConnections:        *totalConnections,
			MaxConcurrentRequests: *globalActive,
			IdleConnTimeout:       time.Duration(*idleExpiry) * time.Second,
		},
	})
	if err != nil {
		panic(err)
	}
	pool, err := worker.New(client, worker.Config{
		Workers:              *workers,
		Capacity:             *capacity,
		ResultCapacity:       *resultCapacity,
		PerOriginConcurrency: *perOrigin,
		JobTimeout:           time.Duration(*jobTimeout) * time.Second,
		ShutdownGrace:        30 * time.Second,
	})
	if err != nil {
		panic(err)
	}
	executableSHA256, buildInfo, err := ownBuildIdentity()
	if err != nil {
		panic(err)
	}
	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(readyOutput{
		Type:                     "ready",
		Implementation:           "go-worker-pilot",
		PID:                      os.Getpid(),
		Runtime:                  runtime.Version(),
		GOOS:                     runtime.GOOS,
		GOARCH:                   runtime.GOARCH,
		GOMAXPROCS:               runtime.GOMAXPROCS(0),
		Workers:                  *workers,
		Capacity:                 *capacity,
		ResultCapacity:           *resultCapacity,
		PerOriginConcurrency:     *perOrigin,
		GlobalActiveRequests:     *globalActive,
		GlobalTotalConnections:   *totalConnections,
		GlobalIdleConnections:    *globalIdle,
		IdleConnectionsPerHost:   *idlePerOrigin,
		PerOriginIdleEnforcement: "go-transport-extra-nonbinding-at-active-origin-cap",
		IdleExpirySeconds:        *idleExpiry,
		RequestTimeoutSeconds:    *requestTimeout,
		JobTimeoutSeconds:        *jobTimeout,
		FileDescriptorSoftLimit:  softLimit,
		ClientBeforeReady:        true,
		PoolBeforeReady:          true,
		ExecutableSHA256:         executableSHA256,
		BuildInfo:                buildInfo,
	}); err != nil {
		panic(err)
	}

	scanner := bufio.NewScanner(os.Stdin)
	buffer := make([]byte, 1024)
	scanner.Buffer(buffer, 16*1024*1024)
	for scanner.Scan() {
		var input command
		if err := json.Unmarshal(scanner.Bytes(), &input); err != nil {
			panic(err)
		}
		switch input.Action {
		case "batch":
			if err := encoder.Encode(runBatch(pool, client, input)); err != nil {
				panic(err)
			}
		case "shutdown":
			ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
			defer cancel()
			if err := pool.Shutdown(ctx); err != nil {
				panic(err)
			}
			client.Close()
			if err := encoder.Encode(map[string]any{"type": "stopped", "implementation": "go-worker-pilot"}); err != nil {
				panic(err)
			}
			return
		default:
			panic("unknown command")
		}
	}
	if err := scanner.Err(); err != nil {
		panic(err)
	}
}
