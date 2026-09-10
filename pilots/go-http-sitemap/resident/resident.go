// Package resident exercises the landed Go sitemap worker as one long-lived,
// credential-free process. It is a hermetic retention probe, not crawler or
// queue authority.
package resident

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const (
	SchemaVersion = 1

	workerCount         = 5
	originCount         = 12
	jobsPerOrigin       = 2
	jobsPerWave         = originCount * jobsPerOrigin
	capacity            = workerCount + 1
	resultCapacity      = workerCount
	perOriginConcurrent = 1
	// Keep this strictly above the worker origin limit so fixture evidence
	// observes worker scheduling rather than transport-side queuing.
	transportPerHost    = workerCount
	maxConnections      = 12
	maxKeepalive        = 10
	maxResponseBytes    = 8 << 20
	maxAggregateBytes   = 8 << 20
	maxRequestsPerJob   = 1
	maxProtocolURLs     = 50_000
	requestTimeout      = 5 * time.Second
	jobTimeout          = 10 * time.Second
	shutdownGrace       = 15 * time.Second
	fixtureCloseWait    = time.Second
	idleConnectionTTL   = 30 * time.Second
	fixtureHandlerDelay = 5 * time.Millisecond
	containerMemory     = int64(384 << 20)

	heapRetentionAbsolute   = uint64(16 << 20)
	rssRetentionAbsolute    = int64(16 << 20)
	cgroupRetentionAbsolute = int64(32 << 20)
	retentionNumerator      = int64(3)
	retentionDenominator    = int64(2)
	fdRetentionAllowance    = 4
	goroutineAllowance      = 4
)

var ErrInvalidConfig = errors.New("invalid resident shadow config")

// Config fixes the workload and observation cadence. The command only exposes
// reviewed duration choices; tests use shorter values through this API.
type Config struct {
	Duration       time.Duration
	CycleInterval  time.Duration
	SampleInterval time.Duration
	RequestCeiling uint64
	URLsPerSitemap int
	HandlerDelay   time.Duration
	SampleLateness time.Duration
	CycleLateness  time.Duration
}

type runtimeTopology struct {
	transport boundedhttp.SharedTransportConfig
	worker    worker.Config
}

func fixedRuntimeTopology() runtimeTopology {
	return runtimeTopology{
		transport: boundedhttp.SharedTransportConfig{
			MaxIdleConns:          maxKeepalive,
			MaxIdleConnsPerHost:   perOriginConcurrent,
			MaxConnsPerHost:       transportPerHost,
			MaxConnections:        maxConnections,
			MaxConcurrentRequests: workerCount,
			IdleConnTimeout:       idleConnectionTTL,
		},
		worker: worker.Config{
			Workers:              workerCount,
			Capacity:             capacity,
			ResultCapacity:       resultCapacity,
			PerOriginConcurrency: perOriginConcurrent,
			JobTimeout:           jobTimeout,
			ShutdownGrace:        shutdownGrace,
		},
	}
}

func validRuntimeTopology(topology runtimeTopology) bool {
	return topology.worker.Workers == workerCount &&
		topology.worker.Capacity == capacity &&
		topology.worker.ResultCapacity == resultCapacity &&
		topology.worker.PerOriginConcurrency == perOriginConcurrent &&
		topology.transport.MaxIdleConns == maxKeepalive &&
		topology.transport.MaxIdleConnsPerHost == perOriginConcurrent &&
		topology.transport.MaxConnsPerHost == transportPerHost &&
		topology.transport.MaxConnsPerHost > topology.worker.PerOriginConcurrency &&
		topology.transport.MaxConnections == maxConnections &&
		topology.transport.MaxConnections >= topology.worker.Workers &&
		topology.transport.MaxConcurrentRequests == topology.worker.Workers &&
		topology.transport.IdleConnTimeout == idleConnectionTTL
}

func DefaultConfig(duration time.Duration) Config {
	return Config{
		Duration:       duration,
		CycleInterval:  15 * time.Second,
		SampleInterval: time.Minute,
		RequestCeiling: 24_000,
		URLsPerSitemap: 2_048,
		HandlerDelay:   fixtureHandlerDelay,
		SampleLateness: 5 * time.Second,
		CycleLateness:  250 * time.Millisecond,
	}
}

type ResourceReport struct {
	ProcessRSSBytes     int64  `json:"process_rss_bytes"`
	ProcessMaxRSSBytes  int64  `json:"process_max_rss_bytes"`
	OpenFDs             int    `json:"open_fds"`
	Goroutines          int    `json:"goroutines"`
	HeapAllocBytes      uint64 `json:"heap_alloc_bytes"`
	HeapInuseBytes      uint64 `json:"heap_inuse_bytes"`
	HeapSysBytes        uint64 `json:"heap_sys_bytes"`
	StackInuseBytes     uint64 `json:"stack_inuse_bytes"`
	RuntimeSysBytes     uint64 `json:"runtime_sys_bytes"`
	GCCount             uint32 `json:"gc_count"`
	GCPauseTotalNS      uint64 `json:"gc_pause_total_ns"`
	CgroupMemoryCurrent *int64 `json:"cgroup_memory_current_bytes"`
	CgroupMemoryPeak    *int64 `json:"cgroup_memory_peak_bytes"`
	CgroupMemoryLimit   *int64 `json:"cgroup_memory_limit_bytes"`
	CgroupOOMEvents     *int64 `json:"cgroup_oom_events"`
	CgroupOOMKillEvents *int64 `json:"cgroup_oom_kill_events"`
}

type WorkerReport struct {
	Accepted    uint64 `json:"accepted"`
	Completed   uint64 `json:"completed"`
	Panics      uint64 `json:"panics"`
	Queued      int64  `json:"queued"`
	InFlight    int64  `json:"in_flight"`
	MaxQueued   int64  `json:"max_queued"`
	MaxInFlight int64  `json:"max_in_flight"`
}

type ResultReport struct {
	Terminal          uint64         `json:"terminal"`
	Succeeded         uint64         `json:"succeeded"`
	Failed            uint64         `json:"failed"`
	Requests          uint64         `json:"requests"`
	WireAttempts      uint64         `json:"wire_attempts"`
	DecodedBytes      uint64         `json:"decoded_bytes"`
	StatusBodyBytes   uint64         `json:"status_body_bytes"`
	CanonicalURLCount uint64         `json:"canonical_url_count"`
	ErrorKinds        map[string]int `json:"error_kinds"`
}

type FixtureReport struct {
	Origins               int    `json:"origins"`
	URLsPerSitemap        int    `json:"urls_per_sitemap"`
	WorkerLimit           int    `json:"worker_limit"`
	WorkerPerOriginLimit  int    `json:"worker_per_origin_limit"`
	TransportPerHostLimit int    `json:"transport_per_host_limit"`
	PayloadSHA256         string `json:"payload_sha256"`
	HandledRequests       uint64 `json:"handled_requests"`
	ActiveRequests        int64  `json:"active_requests"`
	MaxConcurrent         int64  `json:"max_concurrent"`
	MaxPerOrigin          int64  `json:"max_per_origin"`
	FirstWaveBarrierHits  int64  `json:"first_wave_barrier_hits"`
	WavesObserved         uint64 `json:"waves_observed"`
	C5Waves               uint64 `json:"c5_waves"`
	OriginSafeWaves       uint64 `json:"origin_safe_waves"`
	ConnectionReuseWaves  uint64 `json:"connection_reuse_waves"`
	MinHandledPerOrigin   uint64 `json:"min_handled_per_origin"`
	MaxHandledPerOrigin   uint64 `json:"max_handled_per_origin"`
	NewConnections        uint64 `json:"new_connections"`
	ClosedConnections     uint64 `json:"closed_connections"`
	CurrentConnections    int64  `json:"current_connections"`
	MaximumConnections    int64  `json:"maximum_connections"`
}

type Snapshot struct {
	SchemaVersion   int                         `json:"schema_version"`
	Kind            string                      `json:"kind"`
	Phase           string                      `json:"phase"`
	Sequence        uint64                      `json:"sequence"`
	SourceCommit    string                      `json:"source_commit"`
	ImageIdentity   string                      `json:"image_identity"`
	ElapsedMillis   int64                       `json:"elapsed_ms"`
	ExpectedMillis  int64                       `json:"expected_ms"`
	LatenessMillis  int64                       `json:"lateness_ms"`
	CyclesCompleted uint64                      `json:"cycles_completed"`
	Worker          WorkerReport                `json:"worker"`
	Connections     boundedhttp.ConnectionStats `json:"connections"`
	Results         ResultReport                `json:"results"`
	Fixture         FixtureReport               `json:"fixture"`
	Resources       ResourceReport              `json:"resources"`
}

type RetentionPolicy struct {
	ContainerMemoryBytes int64  `json:"container_memory_bytes"`
	HeapAbsoluteBytes    uint64 `json:"heap_absolute_bytes"`
	RSSAbsoluteBytes     int64  `json:"rss_absolute_bytes"`
	CgroupAbsoluteBytes  int64  `json:"cgroup_absolute_bytes"`
	RelativeNumerator    int64  `json:"relative_numerator"`
	RelativeDenominator  int64  `json:"relative_denominator"`
	FDAllowance          int    `json:"fd_allowance"`
	GoroutineAllowance   int    `json:"goroutine_allowance"`
}

type Report struct {
	SchemaVersion       int                         `json:"schema_version"`
	Kind                string                      `json:"kind"`
	SourceCommit        string                      `json:"source_commit"`
	ImageIdentity       string                      `json:"image_identity"`
	RuntimeVersion      string                      `json:"runtime_version"`
	Status              string                      `json:"status"`
	ErrorKind           string                      `json:"error_kind,omitempty"`
	RunDurationMillis   int64                       `json:"run_duration_ms"`
	AgingElapsedMillis  int64                       `json:"aging_elapsed_ms"`
	ConfiguredMillis    int64                       `json:"configured_duration_ms"`
	CycleIntervalMillis int64                       `json:"cycle_interval_ms"`
	CycleLatenessMillis int64                       `json:"cycle_lateness_ms"`
	SampleIntervalMS    int64                       `json:"sample_interval_ms"`
	SamplesEmitted      uint64                      `json:"samples_emitted"`
	CyclesCompleted     uint64                      `json:"cycles_completed"`
	RequestCeiling      uint64                      `json:"request_ceiling"`
	ExpectedCycles      uint64                      `json:"expected_cycles"`
	ExpectedRequests    uint64                      `json:"expected_requests"`
	Worker              WorkerReport                `json:"worker"`
	Connections         boundedhttp.ConnectionStats `json:"connections"`
	Results             ResultReport                `json:"results"`
	Fixture             FixtureReport               `json:"fixture"`
	Startup             ResourceReport              `json:"startup"`
	Baseline            ResourceReport              `json:"baseline"`
	FinalActive         ResourceReport              `json:"final_active"`
	FinalClosed         ResourceReport              `json:"final_closed"`
	OOMEventDelta       int64                       `json:"oom_event_delta"`
	OOMKillEventDelta   int64                       `json:"oom_kill_event_delta"`
	Retention           RetentionPolicy             `json:"retention_policy"`
}

type Sink func(Snapshot) error
type resourceReader func() ResourceReport

type counters struct {
	mu              sync.Mutex
	cyclesCompleted uint64
	terminal        uint64
	succeeded       uint64
	failed          uint64
	requests        uint64
	wireAttempts    uint64
	decodedBytes    uint64
	statusBodyBytes uint64
	canonicalURLs   uint64
	errorKinds      map[string]int
}

type fixtureWave struct {
	barrier         chan struct{}
	barrierOnce     sync.Once
	slowRelease     chan struct{}
	slowReleaseOnce sync.Once
	slowOrigin      int
	initialOrigins  [originCount]bool
	barrierHits     atomic.Int64
	active          atomic.Int64
	maxActive       atomic.Int64
	originActive    []atomic.Int64
	originMax       []atomic.Int64
	originHandled   []atomic.Uint64
	handled         atomic.Uint64
	newAtStart      uint64
}

type fixture struct {
	servers            []*http.Server
	listeners          []net.Listener
	jobs               []worker.Job
	payloadHash        string
	urlDigest          string
	urlCount           int
	handled            atomic.Uint64
	active             atomic.Int64
	maxActive          atomic.Int64
	firstBarrierHits   atomic.Int64
	wavesObserved      atomic.Uint64
	c5Waves            atomic.Uint64
	originSafeWaves    atomic.Uint64
	reuseWaves         atomic.Uint64
	waveMu             sync.RWMutex
	wave               *fixtureWave
	originActive       []atomic.Int64
	originMax          []atomic.Int64
	originHandled      []atomic.Uint64
	newConnections     atomic.Uint64
	closedConnections  atomic.Uint64
	currentConnections atomic.Int64
	maximumConnections atomic.Int64
	serveDone          []chan struct{}
	closeOnce          sync.Once
	closeErr           error
	delay              time.Duration
}

type feedRequest struct {
	cycle uint64
	ctx   context.Context
}

type feedResult struct {
	cycle    uint64
	accepted int
	err      error
}

type resultEvent struct {
	cycle uint64
	err   error
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
	return imageIdentity == "ghcr.io/colophon-group/jobseek-go-sitemap-resident-shadow:sha-"+sourceCommit
}

func Run(ctx context.Context, started time.Time, config Config, sourceCommit, imageIdentity string, sink Sink) Report {
	return run(ctx, started, config, sourceCommit, imageIdentity, sink, readResources)
}

func run(ctx context.Context, started time.Time, config Config, sourceCommit, imageIdentity string, sink Sink, read resourceReader) Report {
	if started.IsZero() {
		started = time.Now()
	}
	report := newReport(started, config, sourceCommit, imageIdentity)
	fail := func(kind string) Report {
		report.Status = "failed"
		report.ErrorKind = kind
		report.RunDurationMillis = time.Since(started).Milliseconds()
		return report
	}
	if ctx == nil || sink == nil || read == nil || !ValidSourceIdentity(sourceCommit, imageIdentity) {
		return fail("identity_or_sink_invalid")
	}
	if err := validateConfig(config); err != nil {
		return fail("config_invalid")
	}
	topology := fixedRuntimeTopology()
	if !validRuntimeTopology(topology) {
		return fail("topology_invalid")
	}
	// Capture cgroup event counters before allocating the fixture so warm-up or
	// fixture initialization cannot hide a recoverable OOM event.
	report.Startup = read()

	probe, err := startFixture(config.URLsPerSitemap, config.HandlerDelay)
	if err != nil {
		return fail("fixture_start")
	}
	defer probe.close()

	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           requestTimeout,
		MaxDecodedBodyBytes:      maxResponseBytes,
		MaxRequests:              maxRequestsPerJob,
		MaxAggregateDecodedBytes: maxAggregateBytes,
		RequireIdentityEncoding:  true,
		AllowPrivateNetwork:      true,
		SharedTransport:          &topology.transport,
	})
	if err != nil {
		return fail("client_config")
	}
	defer client.Close()

	pool, err := worker.New(client, topology.worker)
	if err != nil {
		return fail("worker_config")
	}

	totals := &counters{errorKinds: make(map[string]int)}
	feedRequests := make(chan feedRequest)
	feedResults := make(chan feedResult, 1)
	resultEvents := make(chan resultEvent, jobsPerWave)
	feederDone := make(chan struct{})
	drainerDone := make(chan struct{})
	go runFeeder(pool, probe.jobs, feedRequests, feedResults, feederDone)
	go runDrainer(pool.Results(), probe, totals, resultEvents, drainerDone)

	closed := false
	var shutdownErr error
	shutdown := func() {
		if closed {
			return
		}
		close(feedRequests)
		<-feederDone
		pool.Close()
		<-drainerDone
		client.Close()
		shutdownErr = probe.close()
		runtime.GC()
		report.FinalClosed = read()
		closed = true
	}
	defer shutdown()
	// The first wave fills the shared transport and crosses the fixed c5
	// fixture barrier. It is outside the aging window but remains in exact
	// conservation totals.
	if err := runObservedWave(ctx, 0, probe, feedRequests, feedResults, resultEvents); err != nil {
		shutdown()
		populateReport(&report, pool, client, probe, totals, topology)
		if shutdownErr != nil {
			return fail("fixture_close")
		}
		return fail(classifyControlError(err))
	}
	totals.completeCycle()
	runtime.GC()
	report.Baseline = read()
	baselineSnapshot := makeSnapshot("baseline", 0, started, started, sourceCommit, imageIdentity, pool, client, probe, totals, topology, read)
	baselineSnapshot.Resources = report.Baseline
	if err := sink(baselineSnapshot); err != nil {
		shutdown()
		populateReport(&report, pool, client, probe, totals, topology)
		if shutdownErr != nil {
			return fail("fixture_close")
		}
		return fail("snapshot_write")
	}
	var sampleCount atomic.Uint64
	sampleCount.Store(1)

	agingStarted := time.Now()
	agingDeadline := agingStarted.Add(config.Duration)
	deadline := time.NewTimer(config.Duration)
	defer deadline.Stop()
	periodicCtx, cancelPeriodic := context.WithCancel(ctx)
	periodicErr := make(chan error, 1)
	periodicDone := make(chan struct{})
	go func() {
		defer close(periodicDone)
		sequence := uint64(1)
		for {
			expected := agingStarted.Add(time.Duration(sequence) * config.SampleInterval)
			if !expected.Before(agingDeadline) {
				return
			}
			timer := time.NewTimer(max(time.Until(expected), 0))
			select {
			case <-periodicCtx.Done():
				stopAndDrain(timer)
				return
			case observed := <-timer.C:
				if observed.Sub(expected) > config.SampleLateness {
					periodicErr <- errors.New("sample_late")
					return
				}
				snapshot := makeSnapshot("resident", sequence, started, expected, sourceCommit, imageIdentity, pool, client, probe, totals, topology, read)
				if err := sink(snapshot); err != nil {
					periodicErr <- errors.New("snapshot_write")
					return
				}
				sampleCount.Add(1)
				sequence++
			}
		}
	}()

	cycle := uint64(1)
	stop := false
	errorKind := ""
	for !stop {
		cycleStarted := agingStarted.Add(time.Duration(cycle-1) * config.CycleInterval)
		if !cycleStarted.Before(agingDeadline) {
			select {
			case <-deadline.C:
			case <-ctx.Done():
				errorKind = "canceled"
			case err := <-periodicErr:
				errorKind = err.Error()
			}
			stop = true
			break
		}
		if currentRequests(totals)+jobsPerWave > config.RequestCeiling {
			errorKind = "request_ceiling"
			break
		}
		if delay := time.Until(cycleStarted); delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-timer.C:
			case <-ctx.Done():
				stopAndDrain(timer)
				errorKind = "canceled"
				stop = true
			case err := <-periodicErr:
				stopAndDrain(timer)
				errorKind = err.Error()
				stop = true
			case <-deadline.C:
				stopAndDrain(timer)
				stop = true
			}
		}
		if stop {
			break
		}
		if time.Since(cycleStarted) > config.CycleLateness {
			errorKind = "cycle_late"
			break
		}
		if err := runObservedWave(ctx, cycle, probe, feedRequests, feedResults, resultEvents); err != nil {
			errorKind = classifyControlError(err)
			break
		}
		totals.completeCycle()
		cycle++
		select {
		case <-deadline.C:
			stop = true
		default:
		}
	}

	cancelPeriodic()
	<-periodicDone
	select {
	case err := <-periodicErr:
		if errorKind == "" {
			errorKind = err.Error()
		}
	default:
	}
	report.AgingElapsedMillis = time.Since(agingStarted).Milliseconds()
	runtime.GC()
	report.FinalActive = read()
	activeSnapshot := makeSnapshot("final_active", sampleCount.Load(), started, agingStarted.Add(config.Duration), sourceCommit, imageIdentity, pool, client, probe, totals, topology, read)
	activeSnapshot.Resources = report.FinalActive
	if err := sink(activeSnapshot); err != nil && errorKind == "" {
		errorKind = "snapshot_write"
	} else if err == nil {
		sampleCount.Add(1)
	}

	shutdown()
	populateReport(&report, pool, client, probe, totals, topology)
	report.SamplesEmitted = sampleCount.Load()
	if shutdownErr != nil {
		errorKind = "fixture_close"
	}

	if errorKind == "" {
		errorKind = validateFinal(report)
	}
	if errorKind != "" {
		return fail(errorKind)
	}
	report.Status = "succeeded"
	report.RunDurationMillis = time.Since(started).Milliseconds()
	return report
}

func newReport(started time.Time, config Config, sourceCommit, imageIdentity string) Report {
	return Report{
		SchemaVersion:       SchemaVersion,
		Kind:                "final",
		SourceCommit:        sourceCommit,
		ImageIdentity:       imageIdentity,
		RuntimeVersion:      runtime.Version(),
		Status:              "failed",
		RunDurationMillis:   time.Since(started).Milliseconds(),
		ConfiguredMillis:    config.Duration.Milliseconds(),
		CycleIntervalMillis: config.CycleInterval.Milliseconds(),
		CycleLatenessMillis: config.CycleLateness.Milliseconds(),
		SampleIntervalMS:    config.SampleInterval.Milliseconds(),
		RequestCeiling:      config.RequestCeiling,
		ExpectedCycles:      expectedAgingCycles(config) + 1,
		ExpectedRequests:    (expectedAgingCycles(config) + 1) * uint64(jobsPerWave),
		Retention: RetentionPolicy{
			ContainerMemoryBytes: containerMemory,
			HeapAbsoluteBytes:    heapRetentionAbsolute,
			RSSAbsoluteBytes:     rssRetentionAbsolute,
			CgroupAbsoluteBytes:  cgroupRetentionAbsolute,
			RelativeNumerator:    retentionNumerator,
			RelativeDenominator:  retentionDenominator,
			FDAllowance:          fdRetentionAllowance,
			GoroutineAllowance:   goroutineAllowance,
		},
	}
}

func validateConfig(config Config) error {
	if config.Duration <= 0 || config.CycleInterval <= 0 || config.SampleInterval <= 0 || config.SampleLateness < 0 || config.CycleLateness < 0 || config.CycleLateness >= config.CycleInterval || config.HandlerDelay < 0 || config.HandlerDelay >= jobTimeout || config.URLsPerSitemap <= 0 || config.URLsPerSitemap > maxProtocolURLs || config.RequestCeiling < uint64(jobsPerWave*2) {
		return ErrInvalidConfig
	}
	if (expectedAgingCycles(config)+1)*uint64(jobsPerWave) > config.RequestCeiling {
		return ErrInvalidConfig
	}
	return nil
}

func expectedAgingCycles(config Config) uint64 {
	if config.Duration <= 0 || config.CycleInterval <= 0 {
		return 0
	}
	return uint64((config.Duration-1)/config.CycleInterval) + 1
}

func runFeeder(pool *worker.Pool, jobs []worker.Job, requests <-chan feedRequest, outcomes chan<- feedResult, done chan<- struct{}) {
	defer close(done)
	for request := range requests {
		accepted := 0
		copies := make([]int, len(jobs))
		for _, jobIndex := range waveOrder(request.cycle, len(jobs)) {
			copies[jobIndex]++
			job := jobs[jobIndex]
			job.ID = fmt.Sprintf("fixture-%02d-copy-%d-cycle-%06d", jobIndex+1, copies[jobIndex], request.cycle)
			job.Context = request.ctx
			if err := pool.Submit(request.ctx, job); err != nil {
				outcomes <- feedResult{cycle: request.cycle, accepted: accepted, err: err}
				goto next
			}
			accepted++
		}
		outcomes <- feedResult{cycle: request.cycle, accepted: accepted}
	next:
	}
}

func runDrainer(results <-chan worker.Result, probe *fixture, totals *counters, events chan<- resultEvent, done chan<- struct{}) {
	defer close(done)
	defer close(events)
	for result := range results {
		cycle, parseErr := cycleFromJobID(result.JobID)
		err := validateResult(result, probe.urlCount, probe.urlDigest)
		if parseErr != nil {
			err = parseErr
		}
		totals.record(result, probe.urlCount, err)
		events <- resultEvent{cycle: cycle, err: err}
	}
}

func runWave(ctx context.Context, cycle uint64, requests chan<- feedRequest, outcomes <-chan feedResult, events <-chan resultEvent) error {
	select {
	case requests <- feedRequest{cycle: cycle, ctx: ctx}:
	case <-ctx.Done():
		return ctx.Err()
	}
	accepted := -1
	received := 0
	var firstErr error
	ctxDone := ctx.Done()
	for accepted < 0 || received < accepted {
		select {
		case outcome := <-outcomes:
			if outcome.cycle != cycle {
				return errors.New("feed_cycle_mismatch")
			}
			accepted = outcome.accepted
			if outcome.err != nil && firstErr == nil {
				firstErr = outcome.err
			}
		case event, ok := <-events:
			if !ok {
				return errors.New("results_closed")
			}
			if event.cycle != cycle {
				return errors.New("result_cycle_mismatch")
			}
			received++
			if event.err != nil && firstErr == nil {
				firstErr = event.err
			}
		case <-ctxDone:
			if firstErr == nil {
				firstErr = ctx.Err()
			}
			ctxDone = nil
		}
	}
	if accepted != jobsPerWave || received != jobsPerWave {
		if firstErr != nil {
			return firstErr
		}
		return errors.New("wave_conservation")
	}
	return firstErr
}

func runObservedWave(ctx context.Context, cycle uint64, probe *fixture, requests chan<- feedRequest, outcomes <-chan feedResult, events <-chan resultEvent) error {
	if err := probe.beginWave(cycle); err != nil {
		return err
	}
	runErr := runWave(ctx, cycle, requests, outcomes, events)
	observationErr := probe.endWave(cycle)
	if runErr != nil {
		return runErr
	}
	return observationErr
}

func (c *counters) record(result worker.Result, expectedURLs int, validationErr error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.terminal++
	c.requests += uint64(max(result.Sitemap.TransportMetrics.Requests, 0))
	c.wireAttempts += uint64(max(result.Sitemap.TransportMetrics.WireAttempts, 0))
	c.decodedBytes += uint64(max(result.Sitemap.TransportMetrics.DecodedBytes, 0))
	c.statusBodyBytes += uint64(max(result.Sitemap.TransportMetrics.StatusBodyBytes, 0))
	if validationErr == nil {
		c.succeeded++
		c.canonicalURLs += uint64(expectedURLs)
		return
	}
	c.failed++
	c.errorKinds[typedErrorKind(validationErr)]++
}

func (c *counters) completeCycle() {
	c.mu.Lock()
	c.cyclesCompleted++
	c.mu.Unlock()
}

func (c *counters) snapshot() (uint64, ResultReport) {
	c.mu.Lock()
	defer c.mu.Unlock()
	kinds := make(map[string]int, len(c.errorKinds))
	for key, value := range c.errorKinds {
		kinds[key] = value
	}
	return c.cyclesCompleted, ResultReport{
		Terminal:          c.terminal,
		Succeeded:         c.succeeded,
		Failed:            c.failed,
		Requests:          c.requests,
		WireAttempts:      c.wireAttempts,
		DecodedBytes:      c.decodedBytes,
		StatusBodyBytes:   c.statusBodyBytes,
		CanonicalURLCount: c.canonicalURLs,
		ErrorKinds:        kinds,
	}
}

func currentRequests(c *counters) uint64 {
	_, results := c.snapshot()
	return results.Requests
}

func makeSnapshot(phase string, sequence uint64, started, expected time.Time, sourceCommit, imageIdentity string, pool *worker.Pool, client *boundedhttp.Client, probe *fixture, totals *counters, topology runtimeTopology, read resourceReader) Snapshot {
	cycles, results := totals.snapshot()
	now := time.Now()
	return Snapshot{
		SchemaVersion:   SchemaVersion,
		Kind:            "snapshot",
		Phase:           phase,
		Sequence:        sequence,
		SourceCommit:    sourceCommit,
		ImageIdentity:   imageIdentity,
		ElapsedMillis:   now.Sub(started).Milliseconds(),
		ExpectedMillis:  expected.Sub(started).Milliseconds(),
		LatenessMillis:  max(now.Sub(expected).Milliseconds(), 0),
		CyclesCompleted: cycles,
		Worker:          workerReport(pool.Stats()),
		Connections:     client.ConnectionStats(),
		Results:         results,
		Fixture:         probe.report(topology),
		Resources:       read(),
	}
}

func populateReport(report *Report, pool *worker.Pool, client *boundedhttp.Client, probe *fixture, totals *counters, topology runtimeTopology) {
	report.Worker = workerReport(pool.Stats())
	report.Connections = client.ConnectionStats()
	report.CyclesCompleted, report.Results = totals.snapshot()
	report.Fixture = probe.report(topology)
	if report.Startup.CgroupOOMEvents != nil && report.FinalClosed.CgroupOOMEvents != nil {
		report.OOMEventDelta = *report.FinalClosed.CgroupOOMEvents - *report.Startup.CgroupOOMEvents
	}
	if report.Startup.CgroupOOMKillEvents != nil && report.FinalClosed.CgroupOOMKillEvents != nil {
		report.OOMKillEventDelta = *report.FinalClosed.CgroupOOMKillEvents - *report.Startup.CgroupOOMKillEvents
	}
}

func workerReport(stats worker.Stats) WorkerReport {
	return WorkerReport{
		Accepted:    stats.Accepted,
		Completed:   stats.Completed,
		Panics:      stats.Panics,
		Queued:      stats.Queued,
		InFlight:    stats.InFlight,
		MaxQueued:   stats.MaxQueued,
		MaxInFlight: stats.MaxInFlight,
	}
}

func validateFinal(report Report) string {
	if report.SamplesEmitted != samplesFromDuration(time.Duration(report.ConfiguredMillis)*time.Millisecond, time.Duration(report.SampleIntervalMS)*time.Millisecond) {
		return "sample_invariant"
	}
	if report.AgingElapsedMillis < report.ConfiguredMillis || report.AgingElapsedMillis > report.ConfiguredMillis+int64((5*time.Second)/time.Millisecond) {
		return "duration_invariant"
	}
	if report.CyclesCompleted != report.ExpectedCycles || report.Results.Terminal != report.ExpectedRequests || report.Worker.Accepted != report.Worker.Completed || report.Worker.Completed != report.Results.Terminal || report.Results.Succeeded != report.Results.Terminal || report.Results.Failed != 0 || len(report.Results.ErrorKinds) != 0 || report.Results.Requests != report.Results.Terminal || report.Results.WireAttempts != report.Results.Terminal || report.Results.StatusBodyBytes != 0 || report.Results.CanonicalURLCount != report.Results.Terminal*uint64(report.Fixture.URLsPerSitemap) {
		return "conservation_invariant"
	}
	if report.Worker.Panics != 0 || report.Worker.Queued != 0 || report.Worker.InFlight != 0 || report.Worker.MaxInFlight != workerCount || report.Worker.MaxQueued > capacity {
		return "worker_invariant"
	}
	expectedPerOrigin := report.CyclesCompleted * jobsPerOrigin
	if report.Fixture.Origins != originCount || report.Fixture.WorkerLimit != workerCount || report.Fixture.WorkerPerOriginLimit != perOriginConcurrent || report.Fixture.TransportPerHostLimit != transportPerHost || report.Fixture.TransportPerHostLimit <= report.Fixture.WorkerPerOriginLimit || report.Fixture.HandledRequests != report.Results.Requests || report.Fixture.ActiveRequests != 0 || report.Fixture.MaxConcurrent != workerCount || report.Fixture.MaxPerOrigin != perOriginConcurrent || report.Fixture.FirstWaveBarrierHits != workerCount || report.Fixture.WavesObserved != report.CyclesCompleted || report.Fixture.C5Waves != report.CyclesCompleted || report.Fixture.OriginSafeWaves != report.CyclesCompleted || report.Fixture.ConnectionReuseWaves != report.CyclesCompleted || report.Fixture.MinHandledPerOrigin != expectedPerOrigin || report.Fixture.MaxHandledPerOrigin != expectedPerOrigin || report.Fixture.NewConnections == 0 || report.Fixture.NewConnections >= report.Fixture.HandledRequests || report.Fixture.CurrentConnections != 0 || report.Fixture.ClosedConnections != report.Fixture.NewConnections || report.Fixture.MaximumConnections > maxConnections+workerCount {
		return "fixture_invariant"
	}
	if report.Connections.Open != 0 || report.Connections.InUsePermits != 0 || report.Connections.Waiters != 0 || report.Connections.PermitLimit != maxConnections || report.Connections.MaximumOpen > maxConnections || report.Connections.MaximumInUsePermits > maxConnections {
		return "connection_invariant"
	}
	if runtime.GOOS == "linux" {
		if !validProcess(report.Startup) || !validProcess(report.Baseline) || !validProcess(report.FinalActive) || !validProcess(report.FinalClosed) || !validCgroupEvidence(report) {
			return "cgroup_invariant"
		}
	}
	if exceedsUintRetention(report.Baseline.HeapInuseBytes, report.FinalActive.HeapInuseBytes, heapRetentionAbsolute) || report.FinalActive.Goroutines > report.Baseline.Goroutines+goroutineAllowance {
		return "process_retention"
	}
	if runtime.GOOS == "linux" && (exceedsIntRetention(report.Baseline.ProcessRSSBytes, report.FinalActive.ProcessRSSBytes, rssRetentionAbsolute) || report.FinalActive.OpenFDs > report.Baseline.OpenFDs+fdRetentionAllowance) {
		return "process_retention"
	}
	if report.FinalClosed.Goroutines > report.Startup.Goroutines+goroutineAllowance || runtime.GOOS == "linux" && report.FinalClosed.OpenFDs > report.Startup.OpenFDs+fdRetentionAllowance {
		return "cleanup_retention"
	}
	if report.Baseline.CgroupMemoryCurrent != nil && report.FinalActive.CgroupMemoryCurrent != nil && exceedsIntRetention(*report.Baseline.CgroupMemoryCurrent, *report.FinalActive.CgroupMemoryCurrent, cgroupRetentionAbsolute) {
		return "cgroup_retention"
	}
	return ""
}

func validProcess(resources ResourceReport) bool {
	return resources.ProcessRSSBytes > 0 && resources.ProcessMaxRSSBytes >= resources.ProcessRSSBytes && resources.OpenFDs >= 3 && resources.Goroutines > 0 && resources.HeapInuseBytes > 0 && resources.RuntimeSysBytes > 0
}

func validCgroup(resources ResourceReport) bool {
	return resources.CgroupMemoryCurrent != nil && *resources.CgroupMemoryCurrent > 0 && resources.CgroupMemoryPeak != nil && *resources.CgroupMemoryPeak > 0 && resources.CgroupMemoryLimit != nil && *resources.CgroupMemoryLimit > 0 && resources.CgroupOOMEvents != nil && *resources.CgroupOOMEvents >= 0 && resources.CgroupOOMKillEvents != nil && *resources.CgroupOOMKillEvents >= 0
}

func validCgroupEvidence(report Report) bool {
	resources := []ResourceReport{report.Startup, report.Baseline, report.FinalActive, report.FinalClosed}
	for _, item := range resources {
		if !validCgroup(item) || *item.CgroupMemoryLimit != containerMemory {
			return false
		}
	}
	return report.OOMEventDelta == 0 && report.OOMKillEventDelta == 0 && *report.FinalClosed.CgroupMemoryPeak < containerMemory
}

func exceedsUintRetention(baseline, final, absolute uint64) bool {
	relative := baseline * uint64(retentionNumerator) / uint64(retentionDenominator)
	return final > max(relative, baseline+absolute)
}

func exceedsIntRetention(baseline, final, absolute int64) bool {
	if baseline < 0 || final < 0 {
		return true
	}
	relative := baseline * retentionNumerator / retentionDenominator
	return final > max(relative, baseline+absolute)
}

func startFixture(urlCount int, delay time.Duration) (*fixture, error) {
	payload := buildPayload(urlCount)
	digest := sha256.Sum256(payload)
	urls := make([]string, 0, urlCount)
	for index := 0; index < urlCount; index++ {
		urls = append(urls, fmt.Sprintf("https://fixture.invalid/job/%06d", index))
	}
	probe := &fixture{
		servers:       make([]*http.Server, 0, originCount),
		listeners:     make([]net.Listener, 0, originCount),
		jobs:          make([]worker.Job, 0, originCount),
		payloadHash:   hex.EncodeToString(digest[:]),
		urlDigest:     canonicalURLSHA256(urls),
		urlCount:      urlCount,
		originActive:  make([]atomic.Int64, originCount),
		originMax:     make([]atomic.Int64, originCount),
		originHandled: make([]atomic.Uint64, originCount),
		serveDone:     make([]chan struct{}, 0, originCount),
		delay:         delay,
	}
	for index := 0; index < originCount; index++ {
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		if err != nil {
			probe.close()
			return nil, err
		}
		address := listener.Addr().String()
		if !strings.HasPrefix(address, "127.0.0.1:") {
			_ = listener.Close()
			probe.close()
			return nil, errors.New("fixture listener is not loopback")
		}
		originIndex := index
		mux := http.NewServeMux()
		mux.HandleFunc("GET /sitemap.xml", func(response http.ResponseWriter, request *http.Request) {
			probe.handle(originIndex, payload, response, request)
		})
		server := &http.Server{Handler: mux, ReadHeaderTimeout: time.Second, ConnState: probe.connectionState}
		done := make(chan struct{})
		probe.listeners = append(probe.listeners, listener)
		probe.servers = append(probe.servers, server)
		probe.serveDone = append(probe.serveDone, done)
		probe.jobs = append(probe.jobs, worker.Job{Sitemap: sitemap.Config{
			SitemapURL:          "http://" + address + "/sitemap.xml",
			MaxURLs:             maxProtocolURLs,
			MaxIndexChildren:    1,
			RootMaxAttempts:     1,
			RequireURLSet:       true,
			AllowHTTPForTesting: true,
		}})
		go func() {
			defer close(done)
			_ = server.Serve(listener)
		}()
	}
	if err := validateFixtureJobs(probe.jobs); err != nil {
		probe.close()
		return nil, err
	}
	return probe, nil
}

func validateFixtureJobs(jobs []worker.Job) error {
	if len(jobs) != originCount {
		return errors.New("fixture origin count")
	}
	seen := make(map[string]struct{}, len(jobs))
	for _, job := range jobs {
		address := strings.TrimPrefix(job.Sitemap.SitemapURL, "http://")
		address = strings.TrimSuffix(address, "/sitemap.xml")
		host, _, err := net.SplitHostPort(address)
		if err != nil || host != "127.0.0.1" {
			return errors.New("fixture origin is not loopback")
		}
		if _, exists := seen[address]; exists {
			return errors.New("duplicate fixture origin")
		}
		seen[address] = struct{}{}
	}
	return nil
}

func (f *fixture) beginWave(cycle uint64) error {
	order := waveOrder(cycle, len(f.jobs))
	if len(order) != jobsPerWave || f.active.Load() != 0 {
		return errors.New("wave_state")
	}
	wave := &fixtureWave{
		barrier:       make(chan struct{}),
		slowRelease:   make(chan struct{}),
		slowOrigin:    order[0],
		originActive:  make([]atomic.Int64, originCount),
		originMax:     make([]atomic.Int64, originCount),
		originHandled: make([]atomic.Uint64, originCount),
		newAtStart:    f.newConnections.Load(),
	}
	for _, origin := range order[:workerCount] {
		wave.initialOrigins[origin] = true
	}
	f.waveMu.Lock()
	defer f.waveMu.Unlock()
	if f.wave != nil {
		return errors.New("wave_state")
	}
	f.wave = wave
	return nil
}

func (f *fixture) currentWave() *fixtureWave {
	f.waveMu.RLock()
	defer f.waveMu.RUnlock()
	return f.wave
}

func (f *fixture) endWave(cycle uint64) error {
	f.waveMu.Lock()
	wave := f.wave
	f.wave = nil
	f.waveMu.Unlock()
	if wave == nil || wave.active.Load() != 0 {
		return errors.New("wave_state")
	}
	maximumPerOrigin := int64(0)
	for index := range wave.originMax {
		maximumPerOrigin = max(maximumPerOrigin, wave.originMax[index].Load())
	}
	handled := wave.handled.Load()
	newConnections := f.newConnections.Load() - wave.newAtStart
	f.wavesObserved.Add(1)
	if wave.maxActive.Load() == workerCount {
		f.c5Waves.Add(1)
	}
	if maximumPerOrigin == perOriginConcurrent {
		f.originSafeWaves.Add(1)
	}
	if handled == jobsPerWave && newConnections < handled {
		f.reuseWaves.Add(1)
	}
	if cycle == 0 {
		f.firstBarrierHits.Store(min(wave.barrierHits.Load(), workerCount))
	}
	if handled != jobsPerWave {
		return errors.New("wave_handled")
	}
	if wave.maxActive.Load() != workerCount || wave.barrierHits.Load() < workerCount {
		return errors.New("wave_c5")
	}
	if maximumPerOrigin != perOriginConcurrent {
		return errors.New("wave_origin")
	}
	if newConnections >= handled {
		return errors.New("wave_reuse")
	}
	return nil
}

func (f *fixture) handle(origin int, payload []byte, response http.ResponseWriter, request *http.Request) {
	wave := f.currentWave()
	if wave == nil {
		http.Error(response, "fixture wave unavailable", http.StatusServiceUnavailable)
		return
	}
	active := f.active.Add(1)
	storeMax(&f.maxActive, active)
	originActive := f.originActive[origin].Add(1)
	storeMax(&f.originMax[origin], originActive)
	defer f.active.Add(-1)
	defer f.originActive[origin].Add(-1)
	f.handled.Add(1)
	f.originHandled[origin].Add(1)

	waveActive := wave.active.Add(1)
	storeMax(&wave.maxActive, waveActive)
	waveOriginActive := wave.originActive[origin].Add(1)
	storeMax(&wave.originMax[origin], waveOriginActive)
	defer wave.active.Add(-1)
	defer wave.originActive[origin].Add(-1)
	wave.handled.Add(1)
	waveOriginRequest := wave.originHandled[origin].Add(1)

	hits := wave.barrierHits.Add(1)
	if hits == workerCount {
		wave.barrierOnce.Do(func() { close(wave.barrier) })
	}
	if hits <= workerCount {
		select {
		case <-wave.barrier:
		case <-request.Context().Done():
			return
		}
	}
	if !wave.initialOrigins[origin] || origin == wave.slowOrigin && waveOriginRequest > 1 {
		wave.slowReleaseOnce.Do(func() { close(wave.slowRelease) })
	}
	if origin == wave.slowOrigin && waveOriginRequest == 1 {
		select {
		case <-wave.slowRelease:
		case <-request.Context().Done():
			return
		}
	}
	if f.delay > 0 {
		timer := time.NewTimer(f.delay)
		select {
		case <-timer.C:
		case <-request.Context().Done():
			stopAndDrain(timer)
			return
		}
	}
	response.Header().Set("Content-Type", "application/xml")
	response.Header().Set("Content-Length", strconv.Itoa(len(payload)))
	_, _ = response.Write(payload)
}

func (f *fixture) report(topology runtimeTopology) FixtureReport {
	maximumPerOrigin := int64(0)
	minimumHandled := ^uint64(0)
	maximumHandled := uint64(0)
	for index := range f.originMax {
		maximumPerOrigin = max(maximumPerOrigin, f.originMax[index].Load())
		handled := f.originHandled[index].Load()
		minimumHandled = min(minimumHandled, handled)
		maximumHandled = max(maximumHandled, handled)
	}
	return FixtureReport{
		Origins:               len(f.jobs),
		URLsPerSitemap:        f.urlCount,
		WorkerLimit:           topology.worker.Workers,
		WorkerPerOriginLimit:  topology.worker.PerOriginConcurrency,
		TransportPerHostLimit: topology.transport.MaxConnsPerHost,
		PayloadSHA256:         f.payloadHash,
		HandledRequests:       f.handled.Load(),
		ActiveRequests:        f.active.Load(),
		MaxConcurrent:         f.maxActive.Load(),
		MaxPerOrigin:          maximumPerOrigin,
		FirstWaveBarrierHits:  f.firstBarrierHits.Load(),
		WavesObserved:         f.wavesObserved.Load(),
		C5Waves:               f.c5Waves.Load(),
		OriginSafeWaves:       f.originSafeWaves.Load(),
		ConnectionReuseWaves:  f.reuseWaves.Load(),
		MinHandledPerOrigin:   minimumHandled,
		MaxHandledPerOrigin:   maximumHandled,
		NewConnections:        f.newConnections.Load(),
		ClosedConnections:     f.closedConnections.Load(),
		CurrentConnections:    f.currentConnections.Load(),
		MaximumConnections:    f.maximumConnections.Load(),
	}
}

func (f *fixture) close() error {
	f.closeOnce.Do(func() {
		for _, server := range f.servers {
			_ = server.Close()
		}
		for _, listener := range f.listeners {
			_ = listener.Close()
		}
		for _, done := range f.serveDone {
			<-done
		}
		deadline := time.NewTimer(fixtureCloseWait)
		defer deadline.Stop()
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		for f.currentConnections.Load() != 0 || f.closedConnections.Load() != f.newConnections.Load() {
			select {
			case <-ticker.C:
			case <-deadline.C:
				f.closeErr = errors.New("fixture connections did not close")
				return
			}
		}
	})
	return f.closeErr
}

func (f *fixture) connectionState(_ net.Conn, state http.ConnState) {
	switch state {
	case http.StateNew:
		f.newConnections.Add(1)
		current := f.currentConnections.Add(1)
		storeMax(&f.maximumConnections, current)
	case http.StateHijacked, http.StateClosed:
		f.closedConnections.Add(1)
		f.currentConnections.Add(-1)
	}
}

func buildPayload(urlCount int) []byte {
	var builder strings.Builder
	builder.Grow(urlCount*80 + 128)
	builder.WriteString(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">`)
	for index := 0; index < urlCount; index++ {
		fmt.Fprintf(&builder, "<url><loc>https://fixture.invalid/job/%06d</loc></url>", index)
	}
	builder.WriteString("</urlset>")
	return []byte(builder.String())
}

func validateResult(result worker.Result, expectedURLs int, expectedDigest string) error {
	if result.Err != nil {
		return result.Err
	}
	metrics := result.Sitemap.TransportMetrics
	if metrics.Requests != 1 || metrics.WireAttempts != 1 || metrics.DecodedBytes <= 0 || metrics.DecodedBytes > maxResponseBytes || metrics.StatusBodyBytes != 0 || result.Sitemap.Truncated || len(result.Sitemap.URLs) != expectedURLs {
		return errors.New("result_invariant")
	}
	if canonicalURLSHA256(result.Sitemap.URLs) != expectedDigest || expectedDigest == "" {
		return errors.New("result_digest")
	}
	return nil
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

func orderedIndex(cycle uint64, index, total int) int {
	start := int(cycle % uint64(total))
	if cycle%2 == 0 {
		return (start + index) % total
	}
	return (start - index + total) % total
}

func waveOrder(cycle uint64, total int) []int {
	if total != originCount {
		return nil
	}
	base := make([]int, total)
	for index := range base {
		base[index] = orderedIndex(cycle, index, total)
	}
	order := make([]int, 0, jobsPerWave)
	order = append(order, base[:workerCount]...)
	// Queue a second copy of the first still-active origin behind the five
	// barrier-held workers. Correct per-origin scheduling must keep it queued
	// when another worker becomes available.
	order = append(order, base[0])
	order = append(order, base[workerCount:]...)
	order = append(order, base[1:]...)
	return order
}

func cycleFromJobID(id string) (uint64, error) {
	marker := "-cycle-"
	index := strings.LastIndex(id, marker)
	if index < 0 {
		return 0, errors.New("job_id_invalid")
	}
	cycle, err := strconv.ParseUint(id[index+len(marker):], 10, 64)
	if err != nil {
		return 0, errors.New("job_id_invalid")
	}
	return cycle, nil
}

func classifyControlError(err error) string {
	if err == nil {
		return ""
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "canceled"
	}
	return typedErrorKind(err)
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
	switch err.Error() {
	case "result_invariant", "result_digest", "job_id_invalid", "feed_cycle_mismatch", "result_cycle_mismatch", "results_closed", "wave_conservation", "wave_state", "wave_handled", "wave_c5", "wave_origin", "wave_reuse":
		return err.Error()
	default:
		return "internal"
	}
}

func samplesFromDuration(duration, interval time.Duration) uint64 {
	return uint64((duration-1)/interval) + 2
}

func stopAndDrain(timer *time.Timer) {
	if timer.Stop() {
		return
	}
	select {
	case <-timer.C:
	default:
	}
}

func readResources() ResourceReport {
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	return ResourceReport{
		ProcessRSSBytes:     readProcValue("VmRSS:"),
		ProcessMaxRSSBytes:  readProcValue("VmHWM:"),
		OpenFDs:             readOpenFDs(),
		Goroutines:          runtime.NumGoroutine(),
		HeapAllocBytes:      memory.HeapAlloc,
		HeapInuseBytes:      memory.HeapInuse,
		HeapSysBytes:        memory.HeapSys,
		StackInuseBytes:     memory.StackInuse,
		RuntimeSysBytes:     memory.Sys,
		GCCount:             memory.NumGC,
		GCPauseTotalNS:      memory.PauseTotalNs,
		CgroupMemoryCurrent: readCgroupValue("memory.current"),
		CgroupMemoryPeak:    readCgroupValue("memory.peak"),
		CgroupMemoryLimit:   readCgroupValue("memory.max"),
		CgroupOOMEvents:     readCgroupEvent("oom"),
		CgroupOOMKillEvents: readCgroupEvent("oom_kill"),
	}
}

func readProcValue(name string) int64 {
	contents, err := os.ReadFile("/proc/self/status")
	if err != nil {
		return -1
	}
	for _, line := range strings.Split(string(contents), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == name && fields[2] == "kB" {
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
		if err == nil && value >= 0 {
			return &value
		}
	}
	return nil
}

func storeMax(target *atomic.Int64, candidate int64) {
	for current := target.Load(); candidate > current; current = target.Load() {
		if target.CompareAndSwap(current, candidate) {
			return
		}
	}
}
