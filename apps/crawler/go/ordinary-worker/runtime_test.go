package worker

import (
	"context"
	"errors"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"sync/atomic"
	"testing"
	"time"
)

func fixtureLoopConfig() RuntimeConfig {
	return RuntimeConfig{concurrency: 2, heartbeat: 5 * time.Millisecond, taskTimeout: time.Second, shutdownGrace: 100 * time.Millisecond, cancellationGrace: 50 * time.Millisecond, idleBackoff: time.Millisecond, stallTimeout: time.Second}
}
func awaitRuntime[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case result := <-ch:
		return result
	case <-time.After(3 * time.Second):
		t.Fatal("native runtime fixture timed out")
		var zero T
		return zero
	}
}
func TestRuntimeBoundedClaimsAndSignalDrainKeepsHeartbeatLive(t *testing.T) {
	c := fixtureLoopConfig()
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	m := newRuntimeMetrics(c.concurrency, c.stallTimeout)
	m.ready.Store(true)
	started := make(chan struct{}, 2)
	release := make(chan struct{})
	beats := make(chan struct{}, 100)
	var claims atomic.Int64
	s := runtimeServices{claim: func(context.Context) (*queue.Claim, error) { claims.Add(1); return &queue.Claim{}, nil }, heartbeat: func(context.Context, *queue.Claim) error { beats <- struct{}{}; return nil }, execute: func(ctx context.Context, _ *queue.Claim) (*GreenhouseClaimResult, error) {
		started <- struct{}{}
		<-release
		if ctx.Err() != nil {
			t.Error("signal cancelled live task before drain grace")
		}
		return &GreenhouseClaimResult{Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}}, nil
	}}
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, s, m) }()
	awaitRuntime(t, started)
	awaitRuntime(t, started)
	stop()
	awaitRuntime(t, beats)
	if claims.Load() != 2 {
		t.Fatal("runtime claimed beyond active concurrency")
	}
	close(release)
	if err := awaitRuntime(t, done); err != nil {
		t.Fatal(err)
	}
	if m.active.Load() != 0 || m.drained != 1 || m.timedout != 0 || m.ready.Load() || m.tasks["succeeded"] != 2 {
		t.Fatal("signal drain accounting lost completed claims")
	}
}
func TestRuntimeDrainCancelsUnfinishedWithoutFabricatedSettlement(t *testing.T) {
	c := fixtureLoopConfig()
	c.concurrency = 1
	c.shutdownGrace = 10 * time.Millisecond
	ctx, stop := context.WithCancel(context.Background())
	m := newRuntimeMetrics(1, c.stallTimeout)
	started := make(chan struct{}, 1)
	s := runtimeServices{claim: func(context.Context) (*queue.Claim, error) { return &queue.Claim{}, nil }, heartbeat: func(context.Context, *queue.Claim) error { return nil }, execute: func(ctx context.Context, _ *queue.Claim) (*GreenhouseClaimResult, error) {
		started <- struct{}{}
		<-ctx.Done()
		return &GreenhouseClaimResult{}, ctx.Err()
	}}
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, s, m) }()
	awaitRuntime(t, started)
	stop()
	if err := awaitRuntime(t, done); err != nil {
		t.Fatal(err)
	}
	if m.timedout != 1 || m.cancelled.Load() != 1 || m.tasks["cancelled"] != 1 || m.tasks["succeeded"] != 0 {
		t.Fatal("cancelled inflight task was reported as settled")
	}
}
func TestRuntimeUncooperativeCancellationReturnsBoundedFatal(t *testing.T) {
	c := fixtureLoopConfig()
	c.concurrency = 1
	c.shutdownGrace = 5 * time.Millisecond
	c.cancellationGrace = 5 * time.Millisecond
	ctx, stop := context.WithCancel(context.Background())
	m := newRuntimeMetrics(1, c.stallTimeout)
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	exited := make(chan struct{})
	s := runtimeServices{claim: func(context.Context) (*queue.Claim, error) { return &queue.Claim{}, nil }, heartbeat: func(context.Context, *queue.Claim) error { return nil }, execute: func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error) {
		started <- struct{}{}
		<-release
		close(exited)
		return nil, context.Canceled
	}}
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, s, m) }()
	awaitRuntime(t, started)
	stop()
	if err := awaitRuntime(t, done); !errors.Is(err, ErrDrainTimeout) {
		t.Fatal("uncooperative task prevented bounded process termination", err)
	}
	close(release)
	awaitRuntime(t, exited)
}
func TestRuntimeLostHeartbeatCancelsOwnedWorkAndStopsClaims(t *testing.T) {
	c := fixtureLoopConfig()
	c.concurrency = 1
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	m := newRuntimeMetrics(1, c.stallTimeout)
	var claims atomic.Int64
	s := runtimeServices{claim: func(context.Context) (*queue.Claim, error) { claims.Add(1); return &queue.Claim{}, nil }, heartbeat: func(context.Context, *queue.Claim) error { return queue.ErrAuthorityLost }, execute: func(ctx context.Context, _ *queue.Claim) (*GreenhouseClaimResult, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}}
	if err := runWorkerLoop(ctx, c, s, m); !errors.Is(err, queue.ErrAuthorityLost) {
		t.Fatal("heartbeat authority loss did not stop process", err)
	}
	if claims.Load() != 1 || m.lost != 1 || m.tasks["authority_lost"] != 1 {
		t.Fatal("lost claim was replayed or counted as success")
	}
}
func TestRuntimeTaskDeadlineStopsHeartbeatAndRetainsUnsettledResult(t *testing.T) {
	c := fixtureLoopConfig()
	c.taskTimeout = 15 * time.Millisecond
	m := newRuntimeMetrics(1, c.stallTimeout)
	s := runtimeServices{heartbeat: func(context.Context, *queue.Claim) error { return nil }, execute: func(ctx context.Context, _ *queue.Claim) (*GreenhouseClaimResult, error) {
		<-ctx.Done()
		return &GreenhouseClaimResult{}, ctx.Err()
	}}
	result, err := runLeasedClaim(context.Background(), c, s, &queue.Claim{}, m)
	if !errors.Is(err, context.DeadlineExceeded) || result.Settled || m.extended < 1 {
		t.Fatal("deadline fabricated completion or omitted live renewal")
	}
}
func TestRuntimeSettlementSerializesAgainstLateHeartbeat(t *testing.T) {
	c := fixtureLoopConfig()
	c.heartbeat = time.Millisecond
	m := newRuntimeMetrics(1, c.stallTimeout)
	var heartbeatAfterAck atomic.Int64
	var ack atomic.Bool
	s := runtimeServices{heartbeat: func(context.Context, *queue.Claim) error {
		if ack.Load() {
			heartbeatAfterAck.Add(1)
			return queue.ErrAuthorityLost
		}
		return nil
	}, execute: func(ctx context.Context, _ *queue.Claim) (*GreenhouseClaimResult, error) {
		err := settleClaim(ctx, func() error { ack.Store(true); time.Sleep(15 * time.Millisecond); return nil })
		return &GreenhouseClaimResult{Settled: err == nil}, err
	}}
	result, err := runLeasedClaim(context.Background(), c, s, &queue.Claim{}, m)
	if err != nil || !result.Settled || heartbeatAfterAck.Load() != 0 || m.lost != 0 {
		t.Fatal("terminal acknowledgement raced its own lease renewal", err)
	}
}
func TestRuntimePartialClaimErrorRetainsAttemptAndWatchdogStopsStall(t *testing.T) {
	c := fixtureLoopConfig()
	c.concurrency = 1
	c.stallTimeout = 20 * time.Millisecond
	m := newRuntimeMetrics(1, c.stallTimeout)
	var executed atomic.Int64
	s := runtimeServices{claim: func(context.Context) (*queue.Claim, error) { return &queue.Claim{}, queue.ErrObservation }, heartbeat: func(context.Context, *queue.Claim) error { return nil }, execute: func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error) { executed.Add(1); return nil, nil }}
	if err := runWorkerLoop(context.Background(), c, s, m); err == nil {
		t.Fatal("claim outage never stopped unhealthy process")
	}
	if executed.Load() != 0 || m.claimsFailed == 0 {
		t.Fatal("partial unacknowledged claim was executed")
	}
}

func TestRuntimePerTaskDeadlineBoundsUncooperativeWorkWithoutGlobalStall(t *testing.T) {
	c := fixtureLoopConfig()
	c.taskTimeout = 5 * time.Millisecond
	c.cancellationGrace = 5 * time.Millisecond
	m := newRuntimeMetrics(1, c.stallTimeout)
	release := make(chan struct{})
	exited := make(chan struct{})
	s := runtimeServices{heartbeat: func(context.Context, *queue.Claim) error { return nil }, execute: func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error) {
		<-release
		close(exited)
		return nil, nil
	}}
	result, err := runLeasedClaim(context.Background(), c, s, &queue.Claim{}, m)
	if !errors.Is(err, ErrDrainTimeout) || result != nil {
		t.Fatal("individual hung task escaped bounded deadline", err)
	}
	close(release)
	awaitRuntime(t, exited)
}
