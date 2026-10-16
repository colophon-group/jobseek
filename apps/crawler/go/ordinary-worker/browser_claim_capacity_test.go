package worker

import (
	"context"
	"errors"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestBrowserClaimCapacityKeepsHTTPProgressDuringSlowRendering(t *testing.T) {
	for _, concurrency := range []int{20, 60} {
		t.Run(strconv.Itoa(concurrency), func(t *testing.T) {
			c := fixtureLoopConfig()
			c.concurrency = concurrency
			c.taskTimeout, c.stallTimeout = 3*time.Second, 3*time.Second
			ctx, stop := context.WithCancel(context.Background())
			release := make(chan struct{})
			var once sync.Once
			defer func() { stop(); once.Do(func() { close(release) }) }()
			b := &browserClaimCapacity{slots: make(chan struct{}, renderedConcurrency)}
			var lanes sync.Map
			var browserClaims, httpCompleted atomic.Int64
			pop := func(_ context.Context, lane queue.WorkerType) (*queue.Claim, error) {
				claim := &queue.Claim{}
				lanes.Store(claim, lane)
				if lane == queue.Browser {
					browserClaims.Add(1)
				}
				return claim, nil
			}
			s := runtimeServices{
				claim:        func(ctx context.Context) (*queue.Claim, error) { return b.claim(ctx, pop) },
				releaseClaim: b.release,
				heartbeat:    func(context.Context, *queue.Claim) error { return nil },
				execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
					lane, _ := lanes.LoadAndDelete(claim)
					if lane == queue.Browser {
						select {
						case <-release:
						case <-ctx.Done():
							return nil, ctx.Err()
						}
					} else {
						httpCompleted.Add(1)
						runtime.Gosched()
					}
					return &GreenhouseClaimResult{Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}}, nil
				},
			}
			m := newRuntimeMetrics(c.concurrency, c.stallTimeout)
			done := make(chan error, 1)
			go func() { done <- runWorkerLoop(ctx, c, s, m) }()
			deadline := time.Now().Add(time.Second)
			for (browserClaims.Load() < renderedConcurrency || httpCompleted.Load() == 0) && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			before := httpCompleted.Load()
			deadline = time.Now().Add(time.Second)
			for httpCompleted.Load() <= before+20 && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			if browserClaims.Load() != renderedConcurrency || httpCompleted.Load() <= before+20 {
				t.Fatal("slow browser claims exceeded renderer capacity or starved ready HTTP work")
			}
			stop()
			once.Do(func() { close(release) })
			if err := awaitRuntime(t, done); err != nil {
				t.Fatal(err)
			}
			if len(b.slots) != 0 || m.active.Load() != 0 {
				t.Fatal("drained execution retained browser capacity or active claims")
			}
		})
	}
}

func TestBrowserClaimCapacityEmptyErrorAndCancellationReleaseReservation(t *testing.T) {
	for _, failure := range []error{nil, queue.ErrAuthorityLost, context.Canceled} {
		b := &browserClaimCapacity{slots: make(chan struct{}, 1)}
		b.turn.Store(1) // browser first
		calls := 0
		claim, err := b.claim(context.Background(), func(_ context.Context, lane queue.WorkerType) (*queue.Claim, error) {
			calls++
			if calls == 1 && lane != queue.Browser {
				t.Fatal("fixture did not attempt browser first")
			}
			return nil, failure
		})
		if claim != nil || !errors.Is(err, failure) || len(b.slots) != 0 || (failure != nil && calls != 1) || (failure == nil && calls != 2) {
			t.Fatal("failed or empty pop leaked browser capacity or hid authority failure")
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, err = b.claim(ctx, func(context.Context, queue.WorkerType) (*queue.Claim, error) {
			t.Fatal("cancelled caller popped a new claim")
			return nil, nil
		})
		if !errors.Is(err, context.Canceled) || len(b.slots) != 0 {
			t.Fatal("cancellation leaked capacity")
		}
	}
}

func TestBrowserClaimCapacityFallbackAndReleaseAreBounded(t *testing.T) {
	b := &browserClaimCapacity{slots: make(chan struct{}, 1)}
	var browser, simple int
	pop := func(_ context.Context, lane queue.WorkerType) (*queue.Claim, error) {
		if lane == queue.Browser {
			browser++
			return &queue.Claim{}, nil
		}
		simple++
		return nil, nil
	}
	first, err := b.claim(context.Background(), pop)
	if err != nil || first == nil || browser != 1 || simple != 1 {
		t.Fatal("empty HTTP lane did not fall back to available browser capacity")
	}
	second, err := b.claim(context.Background(), pop)
	if err != nil || second != nil || browser != 1 || simple != 2 {
		t.Fatal("full browser capacity was popped again or prevented HTTP fallback")
	}
	b.release(&queue.Claim{})
	if len(b.slots) != 1 {
		t.Fatal("unrelated claim released browser capacity")
	}
	b.release(first)
	b.release(first)
	if len(b.slots) != 0 {
		t.Fatal("release was not idempotent")
	}
	var disabled *browserClaimCapacity
	_, _ = disabled.claim(context.Background(), pop)
	disabled.release(first)
	if browser != 1 || simple != 3 {
		t.Fatal("disabled renderer attempted browser claims")
	}
}

func TestBrowserClaimCapacityRetainedUntilUncooperativeTaskExitsProcess(t *testing.T) {
	c := fixtureLoopConfig()
	c.concurrency = 1
	c.shutdownGrace, c.cancellationGrace = 5*time.Millisecond, 5*time.Millisecond
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	b := &browserClaimCapacity{slots: make(chan struct{}, 1)}
	b.turn.Store(1)
	started, release := make(chan struct{}), make(chan struct{})
	defer close(release)
	s := runtimeServices{
		claim: func(ctx context.Context) (*queue.Claim, error) {
			return b.claim(ctx, func(context.Context, queue.WorkerType) (*queue.Claim, error) { return &queue.Claim{}, nil })
		},
		releaseClaim: b.release,
		heartbeat:    func(context.Context, *queue.Claim) error { return nil },
		execute: func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error) {
			close(started)
			<-release
			return nil, context.Canceled
		},
	}
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, s, newRuntimeMetrics(1, c.stallTimeout)) }()
	awaitRuntime(t, started)
	stop()
	if err := awaitRuntime(t, done); !errors.Is(err, ErrDrainTimeout) {
		t.Fatal("uncooperative task did not fail closed")
	}
	if len(b.slots) != 1 {
		t.Fatal("uncooperative execution released browser capacity before process exit")
	}
}
