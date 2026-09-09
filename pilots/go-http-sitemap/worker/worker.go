// Package worker runs the non-production sitemap pilot with bounded,
// origin-fair concurrency.
package worker

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

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
)

var (
	ErrClosed        = errors.New("sitemap worker is closed")
	ErrInvalidConfig = errors.New("invalid sitemap worker config")
	ErrInvalidJob    = errors.New("invalid sitemap worker job")
)

// Config fixes every concurrency and buffering bound for a Pool. Capacity is
// the maximum number of accepted jobs that have not yet published a terminal
// result. Results can hold at most ResultCapacity terminal results.
type Config struct {
	Workers        int
	Capacity       int
	ResultCapacity int
	// PerOriginConcurrency is an exact per-origin service limit. Configure it
	// below Workers when one origin must not consume every worker; equality is
	// valid but intentionally makes no cross-origin reservation.
	PerOriginConcurrency int
	JobTimeout           time.Duration
	// ShutdownGrace is the single, nonextendable interval between Close and
	// cancellation of pool-owned work. Jobs still receive terminal results.
	ShutdownGrace time.Duration
}

// Job is immutable after Submit returns. Context controls execution after the
// job has been accepted; the context passed to Submit only controls admission.
// Timeout overrides Config.JobTimeout when it is positive.
type Job struct {
	ID      string
	Context context.Context
	Timeout time.Duration
	Sitemap sitemap.Config
}

// Result is the one terminal outcome for an accepted Job. QueueDuration spans
// acceptance through service start. ServiceDuration covers the processor call
// and excludes time blocked publishing the result to the bounded output.
type Result struct {
	JobID           string
	Origin          string
	Sitemap         sitemap.Result
	Err             error
	AcceptedAt      time.Time
	StartedAt       time.Time
	FinishedAt      time.Time
	QueueDuration   time.Duration
	ServiceDuration time.Duration
}

// Stats is a race-free point-in-time snapshot. Individual fields can move
// between scheduler transitions while Stats is being collected; after Done is
// closed the snapshot is final and internally consistent.
type Stats struct {
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

// Processor is injectable so scheduler behavior can be tested without HTTP.
// Implementations must observe ctx; the Pool does not create a goroutine per
// job and therefore cannot forcibly stop an implementation that ignores it.
type Processor interface {
	Process(context.Context, Job) (sitemap.Result, error)
}

type ProcessorFunc func(context.Context, Job) (sitemap.Result, error)

func (f ProcessorFunc) Process(ctx context.Context, job Job) (sitemap.Result, error) {
	return f(ctx, job)
}

// PanicError reports a contained processor panic without echoing its value,
// which may contain credentials or job data.
type PanicError struct{}

func (*PanicError) Error() string { return "sitemap worker processor panicked" }

type sitemapProcessor struct {
	client *boundedhttp.Client
}

func (p sitemapProcessor) Process(ctx context.Context, job Job) (sitemap.Result, error) {
	runner, err := sitemap.New(p.client, job.Sitemap)
	if err != nil {
		return sitemap.Result{}, err
	}
	return runner.Run(ctx)
}

type metrics struct {
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

type submitRequest struct {
	job    Job
	origin string
}

type queuedJob struct {
	job        Job
	origin     string
	acceptedAt time.Time
}

type completion struct {
	origin string
}

type originState struct {
	queued   []queuedJob
	inFlight int
}

// Pool owns one scheduler goroutine and exactly Config.Workers worker
// goroutines. It never creates a goroutine for an individual input.
type Pool struct {
	config    Config
	processor Processor
	now       func() time.Time
	launch    func(func())

	submits     chan submitRequest
	dispatch    chan queuedJob
	completions chan completion
	results     chan Result
	done        chan struct{}
	closed      chan struct{}
	slots       chan struct{}

	closeMu  sync.RWMutex
	isClosed bool
	workers  sync.WaitGroup
	metrics  metrics
	activeMu sync.Mutex
	active   map[int]context.CancelFunc
	forced   bool
}

// New constructs a pool whose jobs execute the sitemap pilot. The caller owns
// client and must close it after the Pool has drained when shared transport is
// enabled.
func New(client *boundedhttp.Client, config Config) (*Pool, error) {
	if client == nil {
		return nil, fmt.Errorf("%w: nil HTTP client", ErrInvalidConfig)
	}
	return NewWithProcessor(config, sitemapProcessor{client: client})
}

// NewWithProcessor constructs a pool with an injected processor. It is useful
// for deterministic concurrency tests and alternate non-production harnesses.
func NewWithProcessor(config Config, processor Processor) (*Pool, error) {
	return newPool(config, processor, time.Now)
}

func newPool(config Config, processor Processor, now func() time.Time) (*Pool, error) {
	if config.Workers <= 0 || config.Capacity < config.Workers || config.ResultCapacity <= 0 || config.PerOriginConcurrency <= 0 || config.PerOriginConcurrency > config.Workers || config.JobTimeout <= 0 || config.ShutdownGrace <= 0 || processor == nil || now == nil {
		return nil, ErrInvalidConfig
	}
	pool := &Pool{
		config:      config,
		processor:   processor,
		now:         now,
		launch:      func(fn func()) { go fn() },
		submits:     make(chan submitRequest),
		dispatch:    make(chan queuedJob),
		completions: make(chan completion),
		results:     make(chan Result, config.ResultCapacity),
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

// Submit blocks until bounded capacity is available, admissionCtx is done, or
// the Pool closes. A nil error means exactly one terminal Result will be
// published. A non-nil error means the job was not accepted.
func (p *Pool) Submit(admissionCtx context.Context, job Job) error {
	if admissionCtx == nil || strings.TrimSpace(job.ID) == "" || job.Timeout < 0 {
		return ErrInvalidJob
	}
	origin, err := canonicalOrigin(job.Sitemap.SitemapURL)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidJob, err)
	}
	if job.Context == nil {
		job.Context = context.Background()
	}

	select {
	case p.slots <- struct{}{}:
	case <-admissionCtx.Done():
		return admissionCtx.Err()
	case <-p.closed:
		return ErrClosed
	}

	p.closeMu.RLock()
	defer p.closeMu.RUnlock()
	if p.isClosed {
		<-p.slots
		return ErrClosed
	}
	request := submitRequest{job: job, origin: origin}
	select {
	case p.submits <- request:
		return nil
	case <-admissionCtx.Done():
		<-p.slots
		return admissionCtx.Err()
	}
}

// Results returns the bounded result stream. It closes after Close has been
// called and every accepted job has published its terminal result.
func (p *Pool) Results() <-chan Result { return p.results }

// Done closes once graceful draining is complete and Results is closed.
func (p *Pool) Done() <-chan struct{} { return p.done }

// Close stops admission and starts a graceful drain. It is idempotent and does
// not wait; use Shutdown or Done when completion must be observed. Consumers
// must continue draining Results because result backpressure is intentional.
// After ShutdownGrace it cancels executing and queued work. A Processor that
// ignores its context can still prevent draining; Pool cannot forcibly stop it
// without violating the no-goroutine-per-job contract.
func (p *Pool) Close() {
	p.closeMu.Lock()
	defer p.closeMu.Unlock()
	if p.isClosed {
		return
	}
	p.isClosed = true
	close(p.closed)
	close(p.submits)
	deadline := time.Now().Add(p.config.ShutdownGrace)
	p.launch(func() { p.cancelAfterGrace(deadline) })
}

func (p *Pool) cancelAfterGrace(deadline time.Time) {
	if waitUntilOrDone(deadline, p.done) {
		p.cancelActive()
	}
}

func waitUntilOrDone(deadline time.Time, done <-chan struct{}) bool {
	timer := time.NewTimer(max(time.Until(deadline), 0))
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-done:
		return false
	}
}

func (p *Pool) cancelActive() {
	p.activeMu.Lock()
	p.forced = true
	cancels := make([]context.CancelFunc, 0, len(p.active))
	for _, cancel := range p.active {
		cancels = append(cancels, cancel)
	}
	p.activeMu.Unlock()
	for _, cancel := range cancels {
		cancel()
	}
}

func (p *Pool) setActive(workerID int, cancel context.CancelFunc) {
	p.activeMu.Lock()
	p.active[workerID] = cancel
	forced := p.forced
	p.activeMu.Unlock()
	if forced {
		cancel()
	}
}

func (p *Pool) clearActive(workerID int) {
	p.activeMu.Lock()
	delete(p.active, workerID)
	p.activeMu.Unlock()
}

// Shutdown starts graceful draining and waits for it. If ctx expires, the
// drain continues in the background so accepted jobs retain their result
// guarantee.
func (p *Pool) Shutdown(ctx context.Context) error {
	if ctx == nil {
		return ErrInvalidJob
	}
	p.Close()
	select {
	case <-p.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Stats returns a race-free metrics snapshot.
func (p *Pool) Stats() Stats {
	return Stats{
		Accepted:         p.metrics.accepted.Load(),
		Completed:        p.metrics.completed.Load(),
		Panics:           p.metrics.panics.Load(),
		Queued:           p.metrics.queued.Load(),
		InFlight:         p.metrics.inFlight.Load(),
		MaxQueued:        p.metrics.maxQueued.Load(),
		MaxInFlight:      p.metrics.maxInFlight.Load(),
		TotalQueueTime:   time.Duration(p.metrics.totalQueueNanos.Load()),
		MaxQueueTime:     time.Duration(p.metrics.maxQueueNanos.Load()),
		TotalServiceTime: time.Duration(p.metrics.totalServiceNanos.Load()),
		MaxServiceTime:   time.Duration(p.metrics.maxServiceNanos.Load()),
	}
}

func (p *Pool) schedule() {
	defer close(p.done)
	states := make(map[string]*originState)
	ready := make([]string, 0)
	queuedCount := 0
	inFlight := 0
	submits := p.submits

	for {
		if submits == nil && queuedCount == 0 && inFlight == 0 {
			close(p.dispatch)
			p.workers.Wait()
			close(p.results)
			return
		}

		readyIndex := -1
		var dispatch chan queuedJob
		var next queuedJob
		if inFlight < p.config.Workers {
			readyIndex = nextReadyIndex(ready, states, p.config.PerOriginConcurrency)
			if readyIndex >= 0 {
				dispatch = p.dispatch
				state := states[ready[readyIndex]]
				next = state.queued[0]
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
				state = &originState{}
				states[request.origin] = state
			}
			if len(state.queued) == 0 {
				ready = append(ready, request.origin)
			}
			state.queued = append(state.queued, queuedJob{
				job:        request.job,
				origin:     request.origin,
				acceptedAt: p.now(),
			})
			queuedCount++
			p.metrics.accepted.Add(1)
			current := p.metrics.queued.Add(1)
			storeMax(&p.metrics.maxQueued, current)

		case dispatch <- next:
			origin := ready[readyIndex]
			state := states[origin]
			state.queued[0] = queuedJob{}
			state.queued = state.queued[1:]
			ready = append(ready[:readyIndex], ready[readyIndex+1:]...)
			if len(state.queued) > 0 {
				ready = append(ready, origin)
			} else {
				state.queued = nil
			}
			state.inFlight++
			queuedCount--
			inFlight++
			p.metrics.queued.Add(-1)
			current := p.metrics.inFlight.Add(1)
			storeMax(&p.metrics.maxInFlight, current)

		case completed := <-p.completions:
			state := states[completed.origin]
			state.inFlight--
			inFlight--
			p.metrics.inFlight.Add(-1)
			<-p.slots
			if state.inFlight == 0 && len(state.queued) == 0 {
				delete(states, completed.origin)
			}
		}
	}
}

func nextReadyIndex(ready []string, states map[string]*originState, limit int) int {
	for index, origin := range ready {
		if states[origin].inFlight < limit {
			return index
		}
	}
	return -1
}

func (p *Pool) work(workerID int) {
	defer p.workers.Done()
	for queued := range p.dispatch {
		startedAt := p.now()
		queueDuration := nonnegativeDuration(startedAt.Sub(queued.acceptedAt))
		p.metrics.totalQueueNanos.Add(int64(queueDuration))
		storeMax(&p.metrics.maxQueueNanos, int64(queueDuration))

		timeout := p.config.JobTimeout
		if queued.job.Timeout > 0 {
			timeout = queued.job.Timeout
		}
		ctx, cancel := context.WithTimeout(queued.job.Context, timeout)
		p.setActive(workerID, cancel)
		value, err, panicked := callProcessor(ctx, p.processor, queued.job)
		if err == nil && ctx.Err() != nil {
			err = ctx.Err()
		}
		p.clearActive(workerID)
		cancel()

		finishedAt := p.now()
		serviceDuration := nonnegativeDuration(finishedAt.Sub(startedAt))
		p.metrics.totalServiceNanos.Add(int64(serviceDuration))
		storeMax(&p.metrics.maxServiceNanos, int64(serviceDuration))
		if panicked {
			p.metrics.panics.Add(1)
		}

		p.results <- Result{
			JobID:           queued.job.ID,
			Origin:          queued.origin,
			Sitemap:         value,
			Err:             err,
			AcceptedAt:      queued.acceptedAt,
			StartedAt:       startedAt,
			FinishedAt:      finishedAt,
			QueueDuration:   queueDuration,
			ServiceDuration: serviceDuration,
		}
		p.metrics.completed.Add(1)
		p.completions <- completion{origin: queued.origin}
	}
}

func callProcessor(ctx context.Context, processor Processor, job Job) (value sitemap.Result, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			value = sitemap.Result{}
			err = &PanicError{}
			panicked = true
		}
	}()
	value, err = processor.Process(ctx, job)
	return value, err, false
}

func canonicalOrigin(rawURL string) (string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.User != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return "", errors.New("sitemap URL must be an absolute HTTP(S) URL without userinfo")
	}
	hostname := strings.ToLower(parsed.Hostname())
	if hostname == "" {
		return "", errors.New("sitemap URL has no hostname")
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

func nonnegativeDuration(duration time.Duration) time.Duration {
	if duration < 0 {
		return 0
	}
	return duration
}

func storeMax(target *atomic.Int64, candidate int64) {
	for current := target.Load(); candidate > current; current = target.Load() {
		if target.CompareAndSwap(current, candidate) {
			return
		}
	}
}
