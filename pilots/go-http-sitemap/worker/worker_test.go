package worker

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
)

func poolConfig(workers, capacity, resultCapacity, perOrigin int) Config {
	return Config{
		Workers:              workers,
		Capacity:             capacity,
		ResultCapacity:       resultCapacity,
		PerOriginConcurrency: perOrigin,
		JobTimeout:           time.Second,
		ShutdownGrace:        time.Second,
	}
}

func testJob(id, origin string) Job {
	return Job{
		ID: id,
		Sitemap: sitemap.Config{
			SitemapURL:       origin + "/sitemap.xml",
			MaxURLs:          10,
			MaxIndexChildren: 2,
		},
	}
}

func submit(t *testing.T, pool *Pool, job Job) {
	t.Helper()
	if err := pool.Submit(context.Background(), job); err != nil {
		t.Fatalf("submit %s: %v", job.ID, err)
	}
}

func collectResults(t *testing.T, pool *Pool) []Result {
	t.Helper()
	var results []Result
	deadline := time.NewTimer(3 * time.Second)
	defer deadline.Stop()
	for {
		select {
		case result, ok := <-pool.Results():
			if !ok {
				return results
			}
			results = append(results, result)
		case <-deadline.C:
			t.Fatal("timed out draining results")
		}
	}
}

func waitFor(t *testing.T, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatal("condition was not met")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestNewRejectsInvalidBounds(t *testing.T) {
	processor := ProcessorFunc(func(context.Context, Job) (sitemap.Result, error) {
		return sitemap.Result{}, nil
	})
	valid := poolConfig(2, 2, 1, 1)

	for name, mutate := range map[string]func(*Config){
		"workers":         func(config *Config) { config.Workers = 0 },
		"capacity":        func(config *Config) { config.Capacity = 0 },
		"results":         func(config *Config) { config.ResultCapacity = 0 },
		"origin zero":     func(config *Config) { config.PerOriginConcurrency = 0 },
		"origin too high": func(config *Config) { config.PerOriginConcurrency = 3 },
		"job timeout":     func(config *Config) { config.JobTimeout = 0 },
		"shutdown grace":  func(config *Config) { config.ShutdownGrace = 0 },
	} {
		t.Run(name, func(t *testing.T) {
			config := valid
			mutate(&config)
			if _, err := NewWithProcessor(config, processor); !errors.Is(err, ErrInvalidConfig) {
				t.Fatalf("error=%v", err)
			}
		})
	}
	if _, err := NewWithProcessor(valid, nil); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("nil processor error=%v", err)
	}
	if _, err := New(nil, valid); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("nil client error=%v", err)
	}
}

func TestPoolBoundsAcceptedWork(t *testing.T) {
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	processor := ProcessorFunc(func(context.Context, Job) (sitemap.Result, error) {
		select {
		case started <- struct{}{}:
		default:
		}
		<-release
		return sitemap.Result{}, nil
	})
	pool, err := NewWithProcessor(poolConfig(1, 2, 2, 1), processor)
	if err != nil {
		t.Fatal(err)
	}

	submit(t, pool, testJob("one", "https://one.example"))
	<-started
	submit(t, pool, testJob("two", "https://two.example"))
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := pool.Submit(ctx, testJob("rejected", "https://three.example")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("third submit error=%v", err)
	}

	pool.Close()
	close(release)
	results := collectResults(t, pool)
	if len(results) != 2 {
		t.Fatalf("results=%d", len(results))
	}
	stats := pool.Stats()
	if stats.Accepted != 2 || stats.Completed != 2 || stats.MaxInFlight != 1 || stats.MaxQueued < 1 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestPoolDispatchesFairlyWithPerOriginConcurrency(t *testing.T) {
	started := make(chan string, 6)
	release := make(chan struct{})
	var mu sync.Mutex
	activeByOrigin := make(map[string]int)
	maxByOrigin := make(map[string]int)
	activeTotal := 0
	maxTotal := 0

	processor := ProcessorFunc(func(_ context.Context, job Job) (sitemap.Result, error) {
		origin, err := canonicalOrigin(job.Sitemap.SitemapURL)
		if err != nil {
			return sitemap.Result{}, err
		}
		mu.Lock()
		activeByOrigin[origin]++
		if activeByOrigin[origin] > maxByOrigin[origin] {
			maxByOrigin[origin] = activeByOrigin[origin]
		}
		activeTotal++
		if activeTotal > maxTotal {
			maxTotal = activeTotal
		}
		mu.Unlock()
		started <- job.ID
		<-release
		mu.Lock()
		activeByOrigin[origin]--
		activeTotal--
		mu.Unlock()
		return sitemap.Result{}, nil
	})

	pool, err := NewWithProcessor(poolConfig(4, 6, 6, 2), processor)
	if err != nil {
		t.Fatal(err)
	}
	for index := range 4 {
		submit(t, pool, testJob(fmt.Sprintf("a-%d", index), "https://a.example"))
	}
	for index := range 2 {
		submit(t, pool, testJob(fmt.Sprintf("b-%d", index), "https://b.example"))
	}

	startedByPrefix := map[byte]int{}
	for range 4 {
		select {
		case id := <-started:
			startedByPrefix[id[0]]++
		case <-time.After(time.Second):
			t.Fatal("four workers did not start")
		}
	}
	if startedByPrefix['a'] != 2 || startedByPrefix['b'] != 2 {
		t.Fatalf("starts=%v", startedByPrefix)
	}

	pool.Close()
	close(release)
	results := collectResults(t, pool)
	if len(results) != 6 {
		t.Fatalf("results=%d", len(results))
	}
	mu.Lock()
	defer mu.Unlock()
	if maxByOrigin["https://a.example"] > 2 || maxByOrigin["https://b.example"] > 2 || maxTotal != 4 {
		t.Fatalf("max_by_origin=%v max_total=%d", maxByOrigin, maxTotal)
	}
	stats := pool.Stats()
	if stats.MaxInFlight != 4 || stats.MaxQueued < 2 || stats.TotalQueueTime <= 0 || stats.TotalServiceTime <= 0 || stats.MaxServiceTime <= 0 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestResultBackpressureHasNoHiddenUnboundedBacklog(t *testing.T) {
	processor := ProcessorFunc(func(context.Context, Job) (sitemap.Result, error) {
		return sitemap.Result{}, nil
	})
	pool, err := NewWithProcessor(poolConfig(2, 2, 1, 1), processor)
	if err != nil {
		t.Fatal(err)
	}

	for index := range 3 {
		submit(t, pool, testJob(fmt.Sprintf("job-%d", index), fmt.Sprintf("https://%d.example", index)))
	}
	waitFor(t, func() bool { return pool.Stats().Completed == 1 })
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := pool.Submit(ctx, testJob("blocked", "https://blocked.example")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("fourth submit error=%v", err)
	}
	if stats := pool.Stats(); stats.Accepted != 3 || stats.Completed != 1 || len(pool.results) != 1 {
		t.Fatalf("stats=%+v buffered_results=%d", stats, len(pool.results))
	}

	pool.Close()
	results := collectResults(t, pool)
	if len(results) != 3 {
		t.Fatalf("results=%d", len(results))
	}
}

func TestEveryAcceptedJobGetsOneTerminalResult(t *testing.T) {
	processor := ProcessorFunc(func(ctx context.Context, job Job) (sitemap.Result, error) {
		switch job.ID {
		case "panic":
			panic("secret panic value")
		case "success":
			return sitemap.Result{URLs: []string{"https://job.example/1"}}, nil
		default:
			<-ctx.Done()
			return sitemap.Result{}, ctx.Err()
		}
	})
	config := poolConfig(3, 4, 4, 2)
	config.JobTimeout = 25 * time.Millisecond
	pool, err := NewWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}

	canceledContext, cancel := context.WithCancel(context.Background())
	cancel()
	jobs := []Job{
		testJob("panic", "https://panic.example"),
		testJob("success", "https://success.example"),
		testJob("timeout", "https://timeout.example"),
		testJob("canceled", "https://canceled.example"),
	}
	jobs[3].Context = canceledContext
	for _, job := range jobs {
		submit(t, pool, job)
	}
	pool.Close()
	results := collectResults(t, pool)

	byID := make(map[string]Result)
	for _, result := range results {
		if _, duplicate := byID[result.JobID]; duplicate {
			t.Fatalf("duplicate terminal result for %s", result.JobID)
		}
		byID[result.JobID] = result
		if result.AcceptedAt.IsZero() || result.StartedAt.Before(result.AcceptedAt) || result.FinishedAt.Before(result.StartedAt) || result.QueueDuration < 0 || result.ServiceDuration < 0 {
			t.Fatalf("invalid timing: %+v", result)
		}
	}
	if len(byID) != len(jobs) {
		t.Fatalf("terminal results=%d", len(byID))
	}
	var panicErr *PanicError
	if !errors.As(byID["panic"].Err, &panicErr) || byID["panic"].Err.Error() != "sitemap worker processor panicked" {
		t.Fatalf("panic result=%v", byID["panic"].Err)
	}
	if byID["success"].Err != nil || len(byID["success"].Sitemap.URLs) != 1 {
		t.Fatalf("success result=%+v", byID["success"])
	}
	if !errors.Is(byID["timeout"].Err, context.DeadlineExceeded) {
		t.Fatalf("timeout result=%v", byID["timeout"].Err)
	}
	if !errors.Is(byID["canceled"].Err, context.Canceled) {
		t.Fatalf("canceled result=%v", byID["canceled"].Err)
	}
	stats := pool.Stats()
	if stats.Accepted != 4 || stats.Completed != 4 || stats.Panics != 1 || stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestCloseGraceCancellationDrainsQueuedJobsWithoutExtending(t *testing.T) {
	processor := ProcessorFunc(func(ctx context.Context, _ Job) (sitemap.Result, error) {
		<-ctx.Done()
		return sitemap.Result{}, ctx.Err()
	})
	config := poolConfig(1, 3, 3, 1)
	config.JobTimeout = 5 * time.Second
	config.ShutdownGrace = 25 * time.Millisecond
	pool, err := NewWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}
	for index := range 3 {
		submit(t, pool, testJob(fmt.Sprintf("job-%d", index), "https://same.example"))
	}

	started := time.Now()
	pool.Close()
	time.Sleep(15 * time.Millisecond)
	pool.Close() // An idempotent Close must not extend the original grace.
	results := collectResults(t, pool)
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("graceful cancellation took %s", elapsed)
	}
	if len(results) != 3 {
		t.Fatalf("results=%d", len(results))
	}
	for _, result := range results {
		if !errors.Is(result.Err, context.Canceled) {
			t.Fatalf("job %s error=%v", result.JobID, result.Err)
		}
	}
	if err := pool.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := pool.Submit(context.Background(), testJob("late", "https://late.example")); !errors.Is(err, ErrClosed) {
		t.Fatalf("late submit error=%v", err)
	}
}

func TestCloseAnchorsGraceDeadlineBeforeWaiterRuns(t *testing.T) {
	processorStarted := make(chan struct{})
	processor := ProcessorFunc(func(ctx context.Context, _ Job) (sitemap.Result, error) {
		close(processorStarted)
		<-ctx.Done()
		return sitemap.Result{}, ctx.Err()
	})
	config := poolConfig(1, 1, 1, 1)
	config.JobTimeout = 5 * time.Second
	config.ShutdownGrace = 80 * time.Millisecond
	pool, err := NewWithProcessor(config, processor)
	if err != nil {
		t.Fatal(err)
	}

	var retained func()
	pool.launch = func(fn func()) {
		// Retain the entire would-be goroutine body. This models arbitrary
		// scheduling delay before the closure executes.
		retained = fn
	}

	submit(t, pool, testJob("delayed-waiter", "https://delay.example"))
	<-processorStarted
	pool.Close()
	if retained == nil {
		t.Fatal("Close did not launch its grace waiter")
	}

	// Execute the retained closure only after the grace measured from Close has
	// expired. A deadline constructed inside retained would wait a fresh 80ms.
	time.Sleep(config.ShutdownGrace + 20*time.Millisecond)
	started := time.Now()
	retained()
	if elapsed := time.Since(started); elapsed >= config.ShutdownGrace/2 {
		t.Fatalf("delayed waiter received a fresh grace interval: elapsed=%s", elapsed)
	}
	results := collectResults(t, pool)
	if len(results) != 1 || !errors.Is(results[0].Err, context.Canceled) {
		t.Fatalf("results=%+v", results)
	}
}

func TestSitemapProcessorUsesSharedHTTPClientAcrossJobs(t *testing.T) {
	var connections atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/xml")
		_, _ = w.Write([]byte(`<?xml version="1.0"?><urlset><url><loc>https://jobs.example/1</loc></url></urlset>`))
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.Start()
	defer server.Close()

	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      1024,
		MaxRequests:              3,
		MaxAggregateDecodedBytes: 2048,
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          2,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        2,
			MaxConcurrentRequests: 2,
			IdleConnTimeout:       time.Minute,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	pool, err := New(client, poolConfig(2, 2, 2, 1))
	if err != nil {
		t.Fatal(err)
	}
	submit(t, pool, testJob("one", server.URL))
	submit(t, pool, testJob("two", server.URL))
	pool.Close()
	results := collectResults(t, pool)
	for _, result := range results {
		if result.Err != nil || len(result.Sitemap.URLs) != 1 {
			t.Fatalf("result=%+v", result)
		}
	}
	if len(results) != 2 || connections.Load() != 1 {
		t.Fatalf("results=%d connections=%d", len(results), connections.Load())
	}
}

func TestCanonicalOriginNormalizesDefaultPortsAndRejectsCredentials(t *testing.T) {
	for rawURL, expected := range map[string]string{
		"http://EXAMPLE.com:80/a":   "http://example.com",
		"https://EXAMPLE.com:443/a": "https://example.com",
		"https://[::1]:8443/a":      "https://[::1]:8443",
	} {
		origin, err := canonicalOrigin(rawURL)
		if err != nil || origin != expected {
			t.Fatalf("origin(%q)=%q, %v", rawURL, origin, err)
		}
	}
	if _, err := canonicalOrigin("https://user:secret@example.com/sitemap.xml"); err == nil {
		t.Fatal("expected userinfo rejection")
	}
}
