package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

var (
	errPoolClosed        = errors.New("lightpanda pool is closed")
	errInvalidPoolConfig = errors.New("invalid lightpanda pool config")
	errInvalidPoolJob    = errors.New("invalid lightpanda pool job")
	errPoolProcessor     = errors.New("lightpanda pool processor returned no terminal result")
)

// PoolConfig fixes all concurrency and buffering bounds. Capacity includes
// accepted jobs until terminal publication. ResultCapacity must cover one full
// accepted window, but callers must still continuously drain Results: across a
// longer pool lifetime, completed windows can fill the bounded result buffer.
type PoolConfig struct {
	Workers        int
	Capacity       int
	ResultCapacity int
	JobTimeout     time.Duration
	ShutdownGrace  time.Duration
}

// PoolJob is cloned behind bounded capacity during Submit. The caller must not
// mutate Input until Submit returns. AdmissionContext controls only admission;
// Context and Timeout own execution after acceptance.
type PoolJob struct {
	ID      string
	Context context.Context
	Timeout time.Duration
	Input   *runtimev1.BrowserExecutionInput
}

// PoolResult is the sole terminal record for one accepted job.
type PoolResult struct {
	JobID           string
	Origin          string
	Runtime         *runtimev1.BrowserResult
	Err             error
	AcceptedAt      time.Time
	StartedAt       time.Time
	FinishedAt      time.Time
	QueueDuration   time.Duration
	ServiceDuration time.Duration
}

// PoolStats is race-free. Once Done is closed, it is an internally consistent
// final snapshot with Accepted == Completed and Queued == InFlight == 0.
type PoolStats struct {
	Accepted         uint64
	Completed        uint64
	Panics           uint64
	Queued           int64
	InFlight         int64
	MaxQueued        int64
	MaxInFlight      int64
	TotalQueueTime   time.Duration
	MaxQueueTime     time.Duration
	TotalServiceTime time.Duration
	MaxServiceTime   time.Duration
}

// PoolProcessor is injectable for scheduler tests. Implementations must obey
// ctx; the pool deliberately creates no goroutine per job and cannot forcibly
// stop an implementation that ignores cancellation.
type PoolProcessor interface {
	Process(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error)
}

type PoolProcessorFunc func(context.Context, *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error)

func (function PoolProcessorFunc) Process(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) (*runtimev1.BrowserResult, error) {
	return function(ctx, input)
}

// PoolPanicError contains no recovered panic text because it may include
// caller input or provider details.
type PoolPanicError struct{}

func (*PoolPanicError) Error() string { return "lightpanda pool processor panicked" }

type adapterProcessor struct {
	adapter *lightpandaadapter.Adapter
}

func (processor adapterProcessor) Process(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) (*runtimev1.BrowserResult, error) {
	return processor.adapter.Execute(ctx, input), nil
}

type poolMetrics struct {
	accepted          atomic.Uint64
	completed         atomic.Uint64
	panics            atomic.Uint64
	queued            atomic.Int64
	inFlight          atomic.Int64
	maxQueued         atomic.Int64
	maxInFlight       atomic.Int64
	totalQueueNanos   atomic.Int64
	maxQueueNanos     atomic.Int64
	totalServiceNanos atomic.Int64
	maxServiceNanos   atomic.Int64
}

type poolSubmit struct {
	job        PoolJob
	origin     string
	acceptedAt time.Time
	accounted  chan struct{}
}

type poolQueued struct {
	job        PoolJob
	origin     string
	acceptedAt time.Time
}

type poolOriginState struct {
	queued   []poolQueued
	inFlight bool
}

// LightpandaPool owns one scheduler and exactly PoolConfig.Workers worker
// goroutines. Each worker invokes one Adapter.Execute only after global and
// per-origin capacity have been assigned.
type LightpandaPool struct {
	config    PoolConfig
	processor PoolProcessor
	now       func() time.Time
	launch    func(func())

	submits     chan poolSubmit
	dispatch    chan poolQueued
	completions chan string
	results     chan PoolResult
	done        chan struct{}
	closed      chan struct{}
	slots       chan struct{}

	closeMu  sync.RWMutex
	isClosed bool
	workers  sync.WaitGroup
	metrics  poolMetrics
	activeMu sync.Mutex
	active   map[int]context.CancelFunc
	forced   bool
}

// NewLightpandaPool wires the real runtime-v1 adapter into the bounded pool.
func NewLightpandaPool(adapter *lightpandaadapter.Adapter, config PoolConfig) (*LightpandaPool, error) {
	if adapter == nil {
		return nil, fmt.Errorf("%w: nil adapter", errInvalidPoolConfig)
	}
	return NewLightpandaPoolWithProcessor(config, adapterProcessor{adapter: adapter})
}

// NewLightpandaPoolWithProcessor constructs a pool for deterministic tests.
func NewLightpandaPoolWithProcessor(config PoolConfig, processor PoolProcessor) (*LightpandaPool, error) {
	return newLightpandaPool(config, processor, time.Now)
}

func newLightpandaPool(config PoolConfig, processor PoolProcessor, now func() time.Time) (*LightpandaPool, error) {
	if config.Workers <= 0 || config.Capacity < config.Workers ||
		config.ResultCapacity < config.Capacity || config.JobTimeout <= 0 ||
		config.ShutdownGrace <= 0 || processor == nil || now == nil {
		return nil, errInvalidPoolConfig
	}
	pool := &LightpandaPool{
		config:      config,
		processor:   processor,
		now:         now,
		launch:      func(function func()) { go function() },
		submits:     make(chan poolSubmit),
		dispatch:    make(chan poolQueued),
		completions: make(chan string),
		results:     make(chan PoolResult, config.ResultCapacity),
		done:        make(chan struct{}),
		closed:      make(chan struct{}),
		slots:       make(chan struct{}, config.Capacity),
		active:      make(map[int]context.CancelFunc, config.Workers),
	}
	pool.workers.Add(config.Workers)
	for workerID := range config.Workers {
		go pool.work(workerID)
	}
	go pool.schedule()
	return pool, nil
}

// Submit blocks until bounded capacity is available. A nil error means the
// input was cloned and exactly one terminal PoolResult will be published.
func (pool *LightpandaPool) Submit(admissionContext context.Context, job PoolJob) error {
	if admissionContext == nil || strings.TrimSpace(job.ID) == "" || job.Timeout < 0 || job.Input == nil {
		return errInvalidPoolJob
	}
	if err := admissionContext.Err(); err != nil {
		return err
	}
	if job.Context == nil {
		job.Context = context.Background()
	}

	select {
	case pool.slots <- struct{}{}:
	case <-admissionContext.Done():
		return admissionContext.Err()
	case <-pool.closed:
		return errPoolClosed
	}
	pool.closeMu.RLock()
	defer pool.closeMu.RUnlock()
	if pool.isClosed {
		<-pool.slots
		return errPoolClosed
	}
	cloned, origin, err := clonePoolInput(job.Input)
	if err != nil {
		<-pool.slots
		return fmt.Errorf("%w: %v", errInvalidPoolJob, err)
	}
	job.Input = cloned

	acceptedAt := pool.now()
	accounted := make(chan struct{})
	select {
	case pool.submits <- poolSubmit{job: job, origin: origin, acceptedAt: acceptedAt, accounted: accounted}:
		<-accounted
		return nil
	case <-admissionContext.Done():
		<-pool.slots
		return admissionContext.Err()
	}
}

func clonePoolInput(input *runtimev1.BrowserExecutionInput) (
	cloned *runtimev1.BrowserExecutionInput,
	origin string,
	err error,
) {
	defer func() {
		if recover() != nil {
			cloned = nil
			origin = ""
			err = errors.New("runtime-v1 input could not be cloned")
		}
	}()
	if input == nil || proto.Size(input) > lightpandaadapter.InputPayloadLimit {
		return nil, "", errors.New("runtime-v1 input is missing or too large")
	}
	cloned = proto.Clone(input).(*runtimev1.BrowserExecutionInput)
	origin, err = canonicalTargetOrigin(cloned.GetPlan().GetTargetUrl())
	if err != nil {
		return nil, "", err
	}
	return cloned, origin, nil
}

// Results returns the bounded terminal stream. Callers must continuously drain
// it until closed; intentional result backpressure can otherwise stop workers
// and prevent the accepted-work drain from completing.
func (pool *LightpandaPool) Results() <-chan PoolResult { return pool.results }

func (pool *LightpandaPool) Done() <-chan struct{} { return pool.done }

// Close rejects new work and begins draining. It is idempotent and returns
// immediately. At one nonextendable ShutdownGrace deadline, executing and
// subsequently dispatched queued jobs are cancelled. The caller must continue
// draining Results until it closes.
func (pool *LightpandaPool) Close() {
	pool.closeMu.Lock()
	defer pool.closeMu.Unlock()
	if pool.isClosed {
		return
	}
	pool.isClosed = true
	close(pool.closed)
	close(pool.submits)
	deadline := time.Now().Add(pool.config.ShutdownGrace)
	pool.launch(func() { pool.cancelAfterGrace(deadline) })
}

func (pool *LightpandaPool) cancelAfterGrace(deadline time.Time) {
	if waitUntilPoolDeadline(deadline, pool.done) {
		pool.cancelActive()
	}
}

func waitUntilPoolDeadline(deadline time.Time, done <-chan struct{}) bool {
	timer := time.NewTimer(max(time.Until(deadline), 0))
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-done:
		return false
	}
}

func (pool *LightpandaPool) cancelActive() {
	pool.activeMu.Lock()
	pool.forced = true
	cancels := make([]context.CancelFunc, 0, len(pool.active))
	for _, cancel := range pool.active {
		cancels = append(cancels, cancel)
	}
	pool.activeMu.Unlock()
	for _, cancel := range cancels {
		cancel()
	}
}

func (pool *LightpandaPool) setActive(workerID int, cancel context.CancelFunc) {
	pool.activeMu.Lock()
	pool.active[workerID] = cancel
	forced := pool.forced
	pool.activeMu.Unlock()
	if forced {
		cancel()
	}
}

func (pool *LightpandaPool) clearActive(workerID int) {
	pool.activeMu.Lock()
	delete(pool.active, workerID)
	pool.activeMu.Unlock()
}

// Shutdown starts draining and waits no longer than ctx. The owned drain
// continues if ctx expires so accepted jobs retain terminal conservation.
func (pool *LightpandaPool) Shutdown(ctx context.Context) error {
	if ctx == nil {
		return errInvalidPoolJob
	}
	pool.Close()
	select {
	case <-pool.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (pool *LightpandaPool) Stats() PoolStats {
	return PoolStats{
		Accepted:         pool.metrics.accepted.Load(),
		Completed:        pool.metrics.completed.Load(),
		Panics:           pool.metrics.panics.Load(),
		Queued:           pool.metrics.queued.Load(),
		InFlight:         pool.metrics.inFlight.Load(),
		MaxQueued:        pool.metrics.maxQueued.Load(),
		MaxInFlight:      pool.metrics.maxInFlight.Load(),
		TotalQueueTime:   time.Duration(pool.metrics.totalQueueNanos.Load()),
		MaxQueueTime:     time.Duration(pool.metrics.maxQueueNanos.Load()),
		TotalServiceTime: time.Duration(pool.metrics.totalServiceNanos.Load()),
		MaxServiceTime:   time.Duration(pool.metrics.maxServiceNanos.Load()),
	}
}

func (pool *LightpandaPool) schedule() {
	defer close(pool.done)
	states := make(map[string]*poolOriginState)
	ready := make([]string, 0)
	queuedCount := 0
	inFlight := 0
	submits := pool.submits

	for {
		if submits == nil && queuedCount == 0 && inFlight == 0 {
			close(pool.dispatch)
			pool.workers.Wait()
			close(pool.results)
			return
		}

		readyIndex := -1
		var dispatch chan poolQueued
		var next poolQueued
		if inFlight < pool.config.Workers {
			readyIndex = nextPoolReadyIndex(ready, states)
			if readyIndex >= 0 {
				dispatch = pool.dispatch
				next = states[ready[readyIndex]].queued[0]
			}
		}

		select {
		case request, ok := <-submits:
			if !ok {
				submits = nil
				continue
			}
			state := states[request.origin]
			if state == nil {
				state = &poolOriginState{}
				states[request.origin] = state
			}
			if len(state.queued) == 0 {
				ready = append(ready, request.origin)
			}
			state.queued = append(state.queued, poolQueued{
				job: request.job, origin: request.origin, acceptedAt: request.acceptedAt,
			})
			queuedCount++
			pool.metrics.accepted.Add(1)
			current := pool.metrics.queued.Add(1)
			storePoolMax(&pool.metrics.maxQueued, current)
			close(request.accounted)

		case dispatch <- next:
			origin := ready[readyIndex]
			state := states[origin]
			state.queued[0] = poolQueued{}
			state.queued = state.queued[1:]
			ready = append(ready[:readyIndex], ready[readyIndex+1:]...)
			if len(state.queued) > 0 {
				ready = append(ready, origin)
			} else {
				state.queued = nil
			}
			state.inFlight = true
			queuedCount--
			inFlight++
			pool.metrics.queued.Add(-1)
			current := pool.metrics.inFlight.Add(1)
			storePoolMax(&pool.metrics.maxInFlight, current)

		case origin := <-pool.completions:
			state := states[origin]
			state.inFlight = false
			inFlight--
			pool.metrics.inFlight.Add(-1)
			<-pool.slots
			if len(state.queued) == 0 {
				delete(states, origin)
			}
		}
	}
}

func nextPoolReadyIndex(ready []string, states map[string]*poolOriginState) int {
	for index, origin := range ready {
		if !states[origin].inFlight {
			return index
		}
	}
	return -1
}

func (pool *LightpandaPool) work(workerID int) {
	defer pool.workers.Done()
	for queued := range pool.dispatch {
		startedAt := pool.now()
		queueDuration := nonnegativePoolDuration(startedAt.Sub(queued.acceptedAt))
		pool.metrics.totalQueueNanos.Add(int64(queueDuration))
		storePoolMax(&pool.metrics.maxQueueNanos, int64(queueDuration))

		timeout := pool.config.JobTimeout
		if queued.job.Timeout > 0 {
			timeout = queued.job.Timeout
		}
		ctx, cancel := context.WithTimeout(queued.job.Context, timeout)
		pool.setActive(workerID, cancel)
		value, err, panicked := callPoolProcessor(ctx, pool.processor, queued.job.Input)
		if value == nil && err == nil && ctx.Err() != nil {
			err = ctx.Err()
		}
		if value == nil && err == nil {
			err = errPoolProcessor
		}
		if err != nil {
			value = nil
		}
		pool.clearActive(workerID)
		cancel()

		finishedAt := pool.now()
		serviceDuration := nonnegativePoolDuration(finishedAt.Sub(startedAt))
		pool.metrics.totalServiceNanos.Add(int64(serviceDuration))
		storePoolMax(&pool.metrics.maxServiceNanos, int64(serviceDuration))
		if panicked {
			pool.metrics.panics.Add(1)
		}
		if value != nil {
			value = proto.Clone(value).(*runtimev1.BrowserResult)
		}

		pool.results <- PoolResult{
			JobID: queued.job.ID, Origin: queued.origin, Runtime: value, Err: err,
			AcceptedAt: queued.acceptedAt, StartedAt: startedAt, FinishedAt: finishedAt,
			QueueDuration: queueDuration, ServiceDuration: serviceDuration,
		}
		pool.metrics.completed.Add(1)
		pool.completions <- queued.origin
	}
}

func callPoolProcessor(
	ctx context.Context,
	processor PoolProcessor,
	input *runtimev1.BrowserExecutionInput,
) (value *runtimev1.BrowserResult, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			value = nil
			err = &PoolPanicError{}
			panicked = true
		}
	}()
	value, err = processor.Process(ctx, input)
	return value, err, false
}

func canonicalTargetOrigin(rawURL string) (string, error) {
	if len(rawURL) == 0 || len(rawURL) > maxURLBytes {
		return "", errors.New("target URL is missing or too large")
	}
	parsed, err := url.ParseRequestURI(rawURL)
	if err != nil || parsed.User != nil || parsed.Host == "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Fragment != "" ||
		strings.Contains(rawURL, "#") {
		return "", errors.New("target URL must be an absolute HTTP(S) URL without userinfo or fragment")
	}
	hostname := strings.ToLower(parsed.Hostname())
	if hostname == "" {
		return "", errors.New("target URL has no hostname")
	}
	port := parsed.Port()
	if (parsed.Scheme == "http" && port == "80") || (parsed.Scheme == "https" && port == "443") {
		port = ""
	}
	host := hostname
	if port != "" {
		host = net.JoinHostPort(hostname, port)
	} else if strings.Contains(hostname, ":") {
		host = "[" + hostname + "]"
	}
	return parsed.Scheme + "://" + host, nil
}

func nonnegativePoolDuration(duration time.Duration) time.Duration {
	if duration < 0 {
		return 0
	}
	return duration
}

func storePoolMax(target *atomic.Int64, candidate int64) {
	for current := target.Load(); candidate > current; current = target.Load() {
		if target.CompareAndSwap(current, candidate) {
			return
		}
	}
}
