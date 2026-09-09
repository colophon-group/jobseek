// Package canary runs the HTTP sitemap worker against a fixed, read-only
// production-shadow cohort. It has no queue, database, or crawler imports.
package canary

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"sort"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const (
	SchemaVersion     = 1
	MaxManifestBytes  = 64 << 10
	MaxManifestJobs   = 16
	maxProtocolURLs   = 50_000
	requestTimeout    = 20 * time.Second
	jobTimeout        = 45 * time.Second
	shutdownGrace     = 55 * time.Second
	maxIndexChildren  = 16
	maxRequestsPerJob = maxIndexChildren + 3
	// Two jobs can execute concurrently inside a 256 MiB container. Keep the
	// aggregate body allowance below 64 MiB across both task-local sessions so
	// XML decoding and URL storage retain explicit headroom.
	maxResponseBytes    = 8 << 20
	maxAggregateBytes   = 32 << 20
	workerCount         = 2
	perOriginConcurrent = 1
)

type Manifest struct {
	SchemaVersion int           `json:"schema_version"`
	Jobs          []ManifestJob `json:"jobs"`
}

type ManifestJob struct {
	ID               string `json:"id"`
	SitemapURL       string `json:"sitemap_url"`
	IncludeLiteral   string `json:"include_literal"`
	ExcludeLiteral   string `json:"exclude_literal"`
	MaxURLs          int    `json:"max_urls"`
	MaxIndexChildren int    `json:"max_index_children"`
}

type Report struct {
	SchemaVersion     int              `json:"schema_version"`
	SourceCommit      string           `json:"source_commit"`
	ImageIdentity     string           `json:"image_identity"`
	ManifestSHA256    string           `json:"manifest_sha256,omitempty"`
	Status            string           `json:"status"`
	ErrorKind         string           `json:"error_kind,omitempty"`
	RunDurationMillis int64            `json:"run_duration_ms"`
	Jobs              []JobReport      `json:"jobs"`
	Worker            WorkerReport     `json:"worker"`
	Connections       ConnectionReport `json:"connections"`
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

type WorkerReport struct {
	Accepted           uint64 `json:"accepted"`
	Completed          uint64 `json:"completed"`
	Panics             uint64 `json:"panics"`
	Queued             int64  `json:"queued"`
	InFlight           int64  `json:"in_flight"`
	MaxQueued          int64  `json:"max_queued"`
	MaxInFlight        int64  `json:"max_in_flight"`
	TotalQueueMillis   int64  `json:"total_queue_ms"`
	TotalServiceMillis int64  `json:"total_service_ms"`
}

type ConnectionReport struct {
	Open                int64 `json:"open"`
	MaximumOpen         int64 `json:"maximum_open"`
	InUsePermits        int64 `json:"in_use_permits"`
	MaximumInUsePermits int64 `json:"maximum_in_use_permits"`
	PermitLimit         int64 `json:"permit_limit"`
	Waiters             int64 `json:"waiters"`
}

var productionJobs = map[string]ManifestJob{
	"snap-fusion-atlantic-recruitee": {
		ID:               "snap-fusion-atlantic-recruitee",
		SitemapURL:       "https://jobsatlanticvcfoodlabs.recruitee.com/sitemap.xml",
		IncludeLiteral:   "fusion-energy-venture",
		MaxURLs:          maxProtocolURLs,
		MaxIndexChildren: maxIndexChildren,
	},
	"verity-breezy": {
		ID:               "verity-breezy",
		SitemapURL:       "https://verity-ag.breezy.hr/sitemap.xml",
		IncludeLiteral:   "/p/",
		MaxURLs:          maxProtocolURLs,
		MaxIndexChildren: maxIndexChildren,
	},
}

var errManifestInvalid = errors.New("invalid shadow canary manifest")

// LoadManifest reads a strictly bounded JSON object. The fixed allowlist
// prevents a replaced or mounted manifest from turning this canary into an
// arbitrary HTTP client.
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
	if manifest.SchemaVersion != SchemaVersion || len(manifest.Jobs) != len(productionJobs) || len(manifest.Jobs) > MaxManifestJobs {
		return errManifestInvalid
	}
	seenIDs := make(map[string]struct{}, len(manifest.Jobs))
	seenURLs := make(map[string]struct{}, len(manifest.Jobs))
	for _, job := range manifest.Jobs {
		expected, ok := productionJobs[job.ID]
		if !ok || job != expected || job.MaxURLs <= 0 || job.MaxURLs > maxProtocolURLs || job.MaxIndexChildren <= 0 || job.MaxIndexChildren > maxIndexChildren {
			return errManifestInvalid
		}
		if _, exists := seenIDs[job.ID]; exists {
			return errManifestInvalid
		}
		if _, exists := seenURLs[job.SitemapURL]; exists {
			return errManifestInvalid
		}
		seenIDs[job.ID] = struct{}{}
		seenURLs[job.SitemapURL] = struct{}{}
	}
	return nil
}

func ValidSourceIdentity(sourceCommit, imageIdentity string) bool {
	if len(sourceCommit) != 40 || imageIdentity == "" {
		return false
	}
	for _, value := range []byte(sourceCommit) {
		if !('0' <= value && value <= '9') && !('a' <= value && value <= 'f') {
			return false
		}
	}
	return imageIdentity == "ghcr.io/colophon-group/jobseek-go-sitemap-shadow:sha-"+sourceCommit
}

// Run executes every accepted job through the bounded concurrent worker. A
// truncation is deliberately a terminal canary failure even though the
// underlying parser returns the bounded prefix successfully.
func Run(ctx context.Context, manifest Manifest, manifestSHA256, sourceCommit, imageIdentity string) Report {
	started := time.Now()
	report := Report{
		SchemaVersion:  SchemaVersion,
		SourceCommit:   sourceCommit,
		ImageIdentity:  imageIdentity,
		ManifestSHA256: manifestSHA256,
		Status:         "failed",
		Jobs:           make([]JobReport, 0, len(manifest.Jobs)),
	}
	finish := func() Report {
		report.RunDurationMillis = time.Since(started).Milliseconds()
		return report
	}
	if ctx == nil || !ValidSourceIdentity(sourceCommit, imageIdentity) {
		report.ErrorKind = "source_identity_invalid"
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
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          workerCount,
			MaxIdleConnsPerHost:   perOriginConcurrent,
			MaxConnsPerHost:       perOriginConcurrent,
			MaxConnections:        workerCount,
			MaxConcurrentRequests: workerCount,
			IdleConnTimeout:       30 * time.Second,
		},
	})
	if err != nil {
		report.ErrorKind = "client_config"
		return finish()
	}
	defer client.Close()

	pool, err := worker.New(client, worker.Config{
		Workers:              workerCount,
		Capacity:             MaxManifestJobs,
		ResultCapacity:       MaxManifestJobs,
		PerOriginConcurrency: perOriginConcurrent,
		JobTimeout:           jobTimeout,
		ShutdownGrace:        shutdownGrace,
	})
	if err != nil {
		report.ErrorKind = "worker_config"
		return finish()
	}

	for _, job := range manifest.Jobs {
		err := pool.Submit(ctx, worker.Job{
			ID:      job.ID,
			Context: ctx,
			Sitemap: sitemap.Config{
				SitemapURL:       job.SitemapURL,
				IncludeLiteral:   job.IncludeLiteral,
				ExcludeLiteral:   job.ExcludeLiteral,
				MaxURLs:          job.MaxURLs,
				MaxIndexChildren: job.MaxIndexChildren,
			},
		})
		if err != nil {
			pool.Close()
			for range pool.Results() {
			}
			client.Close()
			report.ErrorKind = typedErrorKind(err)
			report.Worker = makeWorkerReport(pool.Stats())
			report.Connections = makeConnectionReport(client.ConnectionStats())
			return finish()
		}
	}
	pool.Close()

	allSucceeded := true
	for result := range pool.Results() {
		jobReport := makeJobReport(result)
		if jobReport.Status != "succeeded" {
			allSucceeded = false
		}
		report.Jobs = append(report.Jobs, jobReport)
	}
	sort.Slice(report.Jobs, func(left, right int) bool {
		return report.Jobs[left].ID < report.Jobs[right].ID
	})
	report.Worker = makeWorkerReport(pool.Stats())
	client.Close()
	report.Connections = makeConnectionReport(client.ConnectionStats())
	if len(report.Jobs) != len(manifest.Jobs) || report.Worker.Accepted != uint64(len(manifest.Jobs)) || report.Worker.Completed != uint64(len(manifest.Jobs)) {
		allSucceeded = false
		report.ErrorKind = "result_invariant"
	}
	if allSucceeded {
		report.Status = "succeeded"
	} else if report.ErrorKind == "" {
		report.ErrorKind = "job_failed"
	}
	return finish()
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
	if errors.Is(err, worker.ErrClosed) {
		return "worker_closed"
	}
	if errors.Is(err, worker.ErrInvalidJob) {
		return "worker_invalid_job"
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline"
	}
	if errors.Is(err, context.Canceled) {
		return "canceled"
	}
	return "internal"
}

func makeWorkerReport(stats worker.Stats) WorkerReport {
	return WorkerReport{
		Accepted:           stats.Accepted,
		Completed:          stats.Completed,
		Panics:             stats.Panics,
		Queued:             stats.Queued,
		InFlight:           stats.InFlight,
		MaxQueued:          stats.MaxQueued,
		MaxInFlight:        stats.MaxInFlight,
		TotalQueueMillis:   stats.TotalQueueTime.Milliseconds(),
		TotalServiceMillis: stats.TotalServiceTime.Milliseconds(),
	}
}

func makeConnectionReport(stats boundedhttp.ConnectionStats) ConnectionReport {
	return ConnectionReport{
		Open:                stats.Open,
		MaximumOpen:         stats.MaximumOpen,
		InUsePermits:        stats.InUsePermits,
		MaximumInUsePermits: stats.MaximumInUsePermits,
		PermitLimit:         stats.PermitLimit,
		Waiters:             stats.Waiters,
	}
}

func FailureReport(sourceCommit, imageIdentity, kind string, started time.Time) Report {
	if kind == "" {
		kind = "internal"
	}
	return Report{
		SchemaVersion:     SchemaVersion,
		SourceCommit:      sourceCommit,
		ImageIdentity:     imageIdentity,
		Status:            "failed",
		ErrorKind:         kind,
		RunDurationMillis: time.Since(started).Milliseconds(),
		Jobs:              []JobReport{},
	}
}
