package queueworker

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const testLeaseTTLMS int64 = 500

type clientFunc func(context.Context, queuev2.RedisCandidateOperation) (queuev2.RedisTransition, error)

func (f clientFunc) Execute(
	ctx context.Context,
	operation queuev2.RedisCandidateOperation,
) (queuev2.RedisTransition, error) {
	return f(ctx, operation)
}

type fakeQueue struct {
	mu sync.Mutex

	claimed         map[string]string
	heartbeatResult map[string]queuev2.RedisTransition
	claimCalls      int
	heartbeatCalls  int
	terminalCalls   int
	sequence        int64
	terminal        []queuev2.RedisCandidateOperation
}

func newFakeQueue() *fakeQueue {
	return &fakeQueue{
		claimed:         make(map[string]string),
		heartbeatResult: make(map[string]queuev2.RedisTransition),
	}
}

func (q *fakeQueue) Execute(
	_ context.Context,
	operation queuev2.RedisCandidateOperation,
) (queuev2.RedisTransition, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	switch operation.Kind {
	case "claim":
		q.claimCalls++
		if _, exists := q.claimed[operation.TaskID]; exists {
			return queuev2.RedisTransition{
				Decision: queuev2.RedisNotCurrent, Reason: "state_mismatch", ServerTimeMS: 1_000,
			}, nil
		}
		q.sequence++
		token := fmt.Sprintf("%d:%d", operation.Route.RoutingEpoch, q.sequence)
		q.claimed[operation.TaskID] = token
		return acceptedClaim(token, operation.LeaseTTLMS), nil
	case "heartbeat":
		q.heartbeatCalls++
		if transition, ok := q.heartbeatResult[operation.TaskID]; ok {
			return transition, nil
		}
		if q.claimed[operation.TaskID] != operation.ClaimToken {
			return queuev2.RedisTransition{
				Decision: queuev2.RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 1_000,
			}, nil
		}
		serverTime := operation.PreviousLeaseUntil - 1
		return queuev2.RedisTransition{
			Decision: queuev2.RedisAccepted, Reason: "lease_extended",
			ServerTimeMS: serverTime, ClaimToken: operation.ClaimToken,
			Value: serverTime + operation.LeaseTTLMS, HasValue: true,
		}, nil
	case "complete", "reschedule":
		q.terminalCalls++
		q.terminal = append(q.terminal, operation)
		if q.claimed[operation.TaskID] != operation.ClaimToken {
			return queuev2.RedisTransition{
				Decision: queuev2.RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 3_000,
			}, nil
		}
		delete(q.claimed, operation.TaskID)
		transition := queuev2.RedisTransition{
			Decision: queuev2.RedisAccepted, ServerTimeMS: 3_000, ClaimToken: operation.ClaimToken,
		}
		if operation.Kind == "complete" {
			transition.Reason = "completed"
		} else {
			transition.Reason = "rescheduled"
			transition.HasValue = true
			transition.Value = transition.ServerTimeMS + operation.RescheduleDelayMS
		}
		return transition, nil
	default:
		return queuev2.RedisTransition{}, fmt.Errorf("unexpected operation %q", operation.Kind)
	}
}

func (q *fakeQueue) snapshot() (claims, heartbeats, terminals, inflight int) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.claimCalls, q.heartbeatCalls, q.terminalCalls, len(q.claimed)
}

func acceptedClaim(token string, ttlMS int64) queuev2.RedisTransition {
	return queuev2.RedisTransition{
		Decision: queuev2.RedisAccepted, Reason: "claimed", ServerTimeMS: 1_000,
		ClaimToken: token, Value: 1_000 + ttlMS, HasValue: true,
	}
}

func testConfig(workers, capacity, resultCapacity, perOrigin int) Config {
	return Config{
		Pool: worker.Config{
			Workers: workers, Capacity: capacity, ResultCapacity: resultCapacity,
			PerOriginConcurrency: perOrigin, JobTimeout: 2 * time.Second,
			ShutdownGrace: 2 * time.Second,
		},
		Route:      queuev2.Route{ShardID: "sitemap-0", RoutingEpoch: 7, EngineOwner: "go"},
		LeaseTTLMS: testLeaseTTLMS, HeartbeatInterval: 20 * time.Millisecond,
		HeartbeatTimeout: 100 * time.Millisecond, TerminalTimeout: 100 * time.Millisecond,
	}
}

func testCandidate(id, origin string) Candidate {
	return Candidate{
		Job: worker.Job{
			ID: id,
			Sitemap: sitemap.Config{
				SitemapURL: origin + "/sitemap.xml", MaxURLs: 10, MaxIndexChildren: 2,
			},
		},
		ConfigRevision: 11,
	}
}

func submitCandidate(t *testing.T, runner *Runner, candidate Candidate) {
	t.Helper()
	if err := runner.Submit(context.Background(), candidate); err != nil {
		t.Fatalf("submit %s: %v", candidate.Job.ID, err)
	}
}

func drainResults(t *testing.T, runner *Runner) []worker.Result {
	t.Helper()
	var results []worker.Result
	timer := time.NewTimer(4 * time.Second)
	defer timer.Stop()
	for {
		select {
		case result, ok := <-runner.Results():
			if !ok {
				return results
			}
			results = append(results, result)
		case <-timer.C:
			t.Fatal("timed out draining results")
		}
	}
}

func waitFor(t *testing.T, condition func() bool, label string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", label)
		}
		time.Sleep(time.Millisecond)
	}
}

func TestClaimOutcomesFailClosedBeforeExecution(t *testing.T) {
	tests := []struct {
		name       string
		transition queuev2.RedisTransition
		clientErr  error
		wantErr    error
	}{
		{
			name: "fenced",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisFenced, Reason: "routing_epoch_mismatch", ServerTimeMS: 1_000,
			},
			wantErr: ErrClaimNotAccepted,
		},
		{
			name: "not due",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisNotCurrent, Reason: "not_due", ServerTimeMS: 1_000,
				Value: 1_500, HasValue: true,
			},
			wantErr: ErrClaimNotAccepted,
		},
		{
			name: "decoded transport with nil error is ambiguous",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisTransportError, Reason: "redis_error",
			},
			wantErr: ErrClaimAmbiguous,
		},
		{
			name:      "client error is ambiguous",
			clientErr: context.DeadlineExceeded,
			wantErr:   ErrClaimAmbiguous,
		},
		{
			name: "invalid accepted lease delta is ambiguous",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisAccepted, Reason: "claimed", ServerTimeMS: 1_000,
				ClaimToken: "7:1", Value: 1_499, HasValue: true,
			},
			wantErr: ErrClaimAmbiguous,
		},
		{
			name: "malformed rejection is ambiguous",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisNotCurrent, Reason: "state_mismatch", ServerTimeMS: 0,
			},
			wantErr: ErrClaimAmbiguous,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			called := make(chan struct{}, 1)
			client := clientFunc(func(
				_ context.Context,
				operation queuev2.RedisCandidateOperation,
			) (queuev2.RedisTransition, error) {
				if operation.Kind != "claim" {
					t.Fatalf("unexpected operation: %+v", operation)
				}
				called <- struct{}{}
				return test.transition, test.clientErr
			})
			var executions atomic.Int64
			runner, err := New(testConfig(1, 1, 1, 1), client, ExecutorFunc(func(
				context.Context, worker.Job, queuev2.LeaseFence,
			) (sitemap.Result, Disposition, error) {
				executions.Add(1)
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
			}))
			if err != nil {
				t.Fatal(err)
			}
			submitCandidate(t, runner, testCandidate("task", "https://one.example"))
			select {
			case <-called:
			case <-time.After(time.Second):
				t.Fatal("claim not called")
			}
			runner.Close()
			results := drainResults(t, runner)
			if len(results) != 1 || !errors.Is(results[0].Err, test.wantErr) {
				t.Fatalf("results=%+v want error %v", results, test.wantErr)
			}
			if executions.Load() != 0 {
				t.Fatalf("executions=%d", executions.Load())
			}
		})
	}
}

func TestAdmissionBoundsClaimsInflightAndSupervisors(t *testing.T) {
	const workers = 3
	queue := newFakeQueue()
	started := make(chan string, workers)
	release := make(chan struct{})
	var active atomic.Int64
	var maxActive atomic.Int64
	executor := ExecutorFunc(func(
		_ context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		current := active.Add(1)
		for observed := maxActive.Load(); current > observed; observed = maxActive.Load() {
			if maxActive.CompareAndSwap(observed, current) {
				break
			}
		}
		started <- job.ID
		<-release
		active.Add(-1)
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	})
	runner, err := New(testConfig(workers, 8, 8, workers), queue, executor)
	if err != nil {
		t.Fatal(err)
	}
	for index := range 8 {
		submitCandidate(t, runner, testCandidate(
			fmt.Sprintf("task-%d", index), fmt.Sprintf("https://origin-%d.example", index),
		))
	}
	for range workers {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("reserved workers did not execute")
		}
	}
	waitFor(t, func() bool {
		claims, heartbeats, _, inflight := queue.snapshot()
		return claims == workers && heartbeats >= workers && inflight == workers
	}, "bounded claims, heartbeats, and inflight")
	time.Sleep(30 * time.Millisecond)
	claims, _, _, inflight := queue.snapshot()
	if claims != workers || inflight != workers || active.Load() != workers || maxActive.Load() != workers {
		t.Fatalf("claims=%d inflight=%d active=%d max_active=%d", claims, inflight, active.Load(), maxActive.Load())
	}

	// Closing the gate while all permits are occupied makes the five queued
	// processor calls publish local results without ever contacting Redis.
	runner.Close()
	close(release)
	results := drainResults(t, runner)
	if len(results) != 8 {
		t.Fatalf("results=%d", len(results))
	}
	claims, _, terminals, inflight := queue.snapshot()
	if claims != workers || terminals != workers || inflight != 0 {
		t.Fatalf("claims=%d terminals=%d inflight=%d", claims, terminals, inflight)
	}
	closedBeforeClaim := 0
	for _, result := range results {
		if errors.Is(result.Err, ErrClosedBeforeClaim) {
			closedBeforeClaim++
		}
	}
	if closedBeforeClaim != 8-workers {
		t.Fatalf("closed-before-claim results=%d", closedBeforeClaim)
	}
}

func TestDispositionAndTerminalValidation(t *testing.T) {
	fence := queuev2.LeaseFence{
		TaskID: "task", Route: queuev2.Route{ShardID: "s", RoutingEpoch: 7, EngineOwner: "go"},
		ConfigRevision: 11, ClaimToken: "7:1",
	}
	operation, err := terminalOperation(fence, Disposition{
		Kind: DispositionReschedule, RescheduleDelay: time.Nanosecond,
	})
	if err != nil || operation.RescheduleDelayMS != 1 {
		t.Fatalf("positive sub-millisecond delay was not rounded up: %+v %v", operation, err)
	}
	if _, err := terminalOperation(fence, Disposition{
		Kind: DispositionComplete, RescheduleDelay: time.Millisecond,
	}); !errors.Is(err, ErrInvalidDisposition) {
		t.Fatalf("complete delay error=%v", err)
	}
	valid := queuev2.RedisTransition{
		Decision: queuev2.RedisAccepted, Reason: "rescheduled", ServerTimeMS: 1_000,
		ClaimToken: "7:1", Value: 1_001, HasValue: true,
	}
	if err := validateAcceptedTerminal(operation, valid); err != nil {
		t.Fatalf("valid reschedule rejected: %v", err)
	}
	valid.Value++
	if err := validateAcceptedTerminal(operation, valid); !errors.Is(err, ErrTerminalAmbiguous) {
		t.Fatalf("inexact reschedule deadline error=%v", err)
	}
	for _, transition := range []queuev2.RedisTransition{
		{Decision: queuev2.RedisTransportError, Reason: "redis_error"},
		{Decision: queuev2.RedisNotCurrent, Reason: "state_mismatch"},
	} {
		if err := validateAcceptedTerminal(operation, transition); !errors.Is(err, ErrTerminalAmbiguous) {
			t.Fatalf("malformed/transport terminal transition %+v error=%v", transition, err)
		}
	}
}

func TestDuplicateCandidatesOnlyOneExecutes(t *testing.T) {
	queue := newFakeQueue()
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	var executions atomic.Int64
	runner, err := New(testConfig(2, 2, 2, 2), queue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		executions.Add(1)
		started <- struct{}{}
		<-release
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("duplicate", "https://same.example"))
	submitCandidate(t, runner, testCandidate("duplicate", "https://same.example"))
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("accepted duplicate did not execute")
	}
	waitFor(t, func() bool {
		claims, _, _, inflight := queue.snapshot()
		return claims == 2 && inflight == 1
	}, "duplicate CAS rejection")
	close(release)
	waitFor(t, func() bool {
		_, _, terminals, _ := queue.snapshot()
		return terminals == 1
	}, "accepted duplicate terminal")
	runner.Close()
	results := drainResults(t, runner)
	if executions.Load() != 1 || len(results) != 2 {
		t.Fatalf("executions=%d results=%d", executions.Load(), len(results))
	}
	notAccepted := 0
	for _, result := range results {
		if errors.Is(result.Err, ErrClaimNotAccepted) {
			notAccepted++
		}
	}
	if notAccepted != 1 {
		t.Fatalf("claim rejection results=%d: %+v", notAccepted, results)
	}
}

func TestHeartbeatLossCancelsOnlyAffectedExecutor(t *testing.T) {
	queue := newFakeQueue()
	queue.heartbeatResult["lost"] = queuev2.RedisTransition{
		Decision: queuev2.RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 1_200,
	}
	started := make(chan string, 2)
	lostCanceled := make(chan error, 1)
	goodCanceled := make(chan error, 1)
	releaseGood := make(chan struct{})
	executor := ExecutorFunc(func(
		ctx context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		started <- job.ID
		if job.ID == "lost" {
			<-ctx.Done()
			select {
			case <-job.Context.Done():
			default:
				lostCanceled <- errors.New("execution job context was not canceled")
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
			}
			argumentCause := context.Cause(ctx)
			jobCause := context.Cause(job.Context)
			if argumentCause != jobCause {
				lostCanceled <- fmt.Errorf("context causes differ: argument=%v job=%v", argumentCause, jobCause)
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
			}
			var argumentLoss, jobLoss *queuev2.LeaseLostError
			if !errors.As(argumentCause, &argumentLoss) || !errors.As(jobCause, &jobLoss) ||
				argumentLoss != jobLoss {
				lostCanceled <- fmt.Errorf("contexts lack the same retained lease loss: argument=%v job=%v", argumentCause, jobCause)
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
			}
			lostCanceled <- context.Cause(ctx)
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
		}
		select {
		case <-releaseGood:
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
		case <-ctx.Done():
			goodCanceled <- context.Cause(ctx)
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
		}
	})
	runner, err := New(testConfig(2, 2, 2, 1), queue, executor)
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("lost", "https://lost.example"))
	submitCandidate(t, runner, testCandidate("good", "https://good.example"))
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("both jobs did not start")
		}
	}
	select {
	case cause := <-lostCanceled:
		var loss *queuev2.LeaseLostError
		if !errors.As(cause, &loss) {
			t.Fatalf("lost executor cause=%v", cause)
		}
	case <-time.After(time.Second):
		t.Fatal("fenced heartbeat did not cancel affected executor")
	}
	select {
	case cause := <-goodCanceled:
		t.Fatalf("unrelated executor was canceled: %v", cause)
	default:
	}
	close(releaseGood)
	waitFor(t, func() bool {
		_, _, terminals, inflight := queue.snapshot()
		return terminals == 1 && inflight == 1
	}, "only current lease terminal")
	runner.Close()
	results := drainResults(t, runner)
	if len(results) != 2 {
		t.Fatalf("results=%d", len(results))
	}
	for _, operation := range queue.terminal {
		if operation.TaskID != "good" {
			t.Fatalf("lost task reached terminal CAS: %+v", operation)
		}
	}
}

func TestNewRequiresGoEngineOwner(t *testing.T) {
	config := testConfig(1, 1, 1, 1)
	config.Route.EngineOwner = "python"
	queue := newFakeQueue()
	_, err := New(config, queue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("python-owned route error=%v", err)
	}
}

func TestStopJoinsForcedHeartbeatBeforeTerminal(t *testing.T) {
	heartbeatStarted := make(chan struct{})
	heartbeatReturned := make(chan struct{})
	terminalCalled := make(chan struct{})
	var heartbeatOnce sync.Once
	client := clientFunc(func(
		ctx context.Context,
		operation queuev2.RedisCandidateOperation,
	) (queuev2.RedisTransition, error) {
		switch operation.Kind {
		case "claim":
			return acceptedClaim("7:1", operation.LeaseTTLMS), nil
		case "heartbeat":
			heartbeatOnce.Do(func() { close(heartbeatStarted) })
			<-ctx.Done()
			close(heartbeatReturned)
			return queuev2.RedisTransition{}, ctx.Err()
		case "complete":
			select {
			case <-heartbeatReturned:
			default:
				t.Error("terminal raced heartbeat return")
			}
			close(terminalCalled)
			return queuev2.RedisTransition{
				Decision: queuev2.RedisAccepted, Reason: "completed", ServerTimeMS: 2_000,
				ClaimToken: operation.ClaimToken,
			}, nil
		default:
			return queuev2.RedisTransition{}, fmt.Errorf("unexpected operation %q", operation.Kind)
		}
	})
	executor := ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		select {
		case <-heartbeatStarted:
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
		case <-time.After(time.Second):
			return sitemap.Result{}, Disposition{}, errors.New("heartbeat did not start")
		}
	})
	runner, err := New(testConfig(1, 1, 1, 1), client, executor)
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("race", "https://race.example"))
	select {
	case <-terminalCalled:
	case <-time.After(time.Second):
		t.Fatal("terminal was not called after heartbeat join")
	}
	runner.Close()
	results := drainResults(t, runner)
	if len(results) != 1 || results[0].Err != nil {
		t.Fatalf("results=%+v", results)
	}
}

func TestCloseAllowsStartedClaimAndBlocksQueuedClaim(t *testing.T) {
	claimStarted := make(chan struct{})
	releaseClaim := make(chan struct{})
	var claimCalls atomic.Int64
	client := clientFunc(func(
		ctx context.Context,
		operation queuev2.RedisCandidateOperation,
	) (queuev2.RedisTransition, error) {
		switch operation.Kind {
		case "claim":
			if claimCalls.Add(1) != 1 {
				t.Error("queued candidate started a post-close claim")
			}
			close(claimStarted)
			select {
			case <-releaseClaim:
				return acceptedClaim("7:1", operation.LeaseTTLMS), nil
			case <-ctx.Done():
				return queuev2.RedisTransition{}, ctx.Err()
			}
		case "heartbeat":
			serverTime := operation.PreviousLeaseUntil - 1
			return queuev2.RedisTransition{
				Decision: queuev2.RedisAccepted, Reason: "lease_extended",
				ServerTimeMS: serverTime, ClaimToken: operation.ClaimToken,
				Value: serverTime + operation.LeaseTTLMS, HasValue: true,
			}, nil
		case "complete":
			return queuev2.RedisTransition{
				Decision: queuev2.RedisAccepted, Reason: "completed", ServerTimeMS: 2_000,
				ClaimToken: operation.ClaimToken,
			}, nil
		default:
			return queuev2.RedisTransition{}, fmt.Errorf("unexpected operation %q", operation.Kind)
		}
	})
	runner, err := New(testConfig(1, 2, 2, 1), client, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("started", "https://one.example"))
	submitCandidate(t, runner, testCandidate("queued", "https://two.example"))
	select {
	case <-claimStarted:
	case <-time.After(time.Second):
		t.Fatal("first claim did not start")
	}
	runner.Close()
	close(releaseClaim)
	results := drainResults(t, runner)
	if claimCalls.Load() != 1 || len(results) != 2 {
		t.Fatalf("claim calls=%d results=%d", claimCalls.Load(), len(results))
	}
	foundClosed := false
	for _, result := range results {
		foundClosed = foundClosed || errors.Is(result.Err, ErrClosedBeforeClaim)
	}
	if !foundClosed {
		t.Fatalf("queued candidate was not left unclaimed: %+v", results)
	}
}

func TestExecutorPanicStopsSupervisorAndLeavesClaimForReaper(t *testing.T) {
	queue := newFakeQueue()
	panicking := make(chan struct{})
	runner, err := New(testConfig(1, 1, 1, 1), queue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		close(panicking)
		panic("sensitive panic value")
	}))
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("panic", "https://panic.example"))
	select {
	case <-panicking:
	case <-time.After(time.Second):
		t.Fatal("executor did not start")
	}
	waitFor(t, func() bool { return runner.Stats().Completed == 1 }, "panic result publication")
	_, _, terminals, inflight := queue.snapshot()
	if terminals != 0 || inflight != 1 {
		t.Fatalf("terminals=%d inflight=%d", terminals, inflight)
	}
	runner.Close()
	results := drainResults(t, runner)
	var panicErr *worker.PanicError
	if len(results) != 1 || !errors.As(results[0].Err, &panicErr) {
		t.Fatalf("panic result=%+v", results)
	}
}

func TestParentCancellationAndTimeoutDoNotTerminal(t *testing.T) {
	for _, test := range []struct {
		name    string
		timeout time.Duration
		cancel  bool
		wantErr error
	}{
		{name: "caller cancellation", cancel: true, wantErr: context.Canceled},
		{name: "job timeout", timeout: 30 * time.Millisecond, wantErr: context.DeadlineExceeded},
	} {
		t.Run(test.name, func(t *testing.T) {
			queue := newFakeQueue()
			started := make(chan struct{})
			finished := make(chan struct{})
			runner, err := New(testConfig(1, 1, 1, 1), queue, ExecutorFunc(func(
				ctx context.Context,
				_ worker.Job,
				_ queuev2.LeaseFence,
			) (sitemap.Result, Disposition, error) {
				close(started)
				<-ctx.Done()
				close(finished)
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
			}))
			if err != nil {
				t.Fatal(err)
			}
			parent, cancel := context.WithCancel(context.Background())
			candidate := testCandidate("canceled", "https://cancel.example")
			candidate.Job.Context = parent
			candidate.Job.Timeout = test.timeout
			submitCandidate(t, runner, candidate)
			select {
			case <-started:
			case <-time.After(time.Second):
				t.Fatal("executor did not start")
			}
			if test.cancel {
				cancel()
			} else {
				defer cancel()
			}
			select {
			case <-finished:
			case <-time.After(time.Second):
				t.Fatal("processing context was not canceled")
			}
			waitFor(t, func() bool { return runner.Stats().Completed == 1 }, "canceled result")
			runner.Close()
			results := drainResults(t, runner)
			_, _, terminals, inflight := queue.snapshot()
			if len(results) != 1 || !errors.Is(results[0].Err, test.wantErr) ||
				terminals != 0 || inflight != 1 {
				t.Fatalf("results=%+v terminals=%d inflight=%d", results, terminals, inflight)
			}
		})
	}
}

func TestTerminalRejectionAndTransportAreFinal(t *testing.T) {
	for _, test := range []struct {
		name       string
		transition queuev2.RedisTransition
		clientErr  error
		wantErr    error
	}{
		{
			name: "safe rejection",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 2_000,
			},
			wantErr: ErrTerminalNotAccepted,
		},
		{
			name: "decoded transport",
			transition: queuev2.RedisTransition{
				Decision: queuev2.RedisTransportError, Reason: "redis_error",
			},
			wantErr: ErrTerminalAmbiguous,
		},
		{name: "client transport", clientErr: context.DeadlineExceeded, wantErr: ErrTerminalAmbiguous},
	} {
		t.Run(test.name, func(t *testing.T) {
			terminalCalled := make(chan struct{}, 1)
			var terminalCalls atomic.Int64
			client := clientFunc(func(
				_ context.Context,
				operation queuev2.RedisCandidateOperation,
			) (queuev2.RedisTransition, error) {
				switch operation.Kind {
				case "claim":
					return acceptedClaim("7:1", operation.LeaseTTLMS), nil
				case "heartbeat":
					serverTime := operation.PreviousLeaseUntil - 1
					return queuev2.RedisTransition{
						Decision: queuev2.RedisAccepted, Reason: "lease_extended",
						ServerTimeMS: serverTime, ClaimToken: operation.ClaimToken,
						Value: serverTime + operation.LeaseTTLMS, HasValue: true,
					}, nil
				case "complete":
					terminalCalls.Add(1)
					terminalCalled <- struct{}{}
					return test.transition, test.clientErr
				default:
					return queuev2.RedisTransition{}, fmt.Errorf("unexpected operation %q", operation.Kind)
				}
			})
			runner, err := New(testConfig(1, 1, 1, 1), client, ExecutorFunc(func(
				context.Context, worker.Job, queuev2.LeaseFence,
			) (sitemap.Result, Disposition, error) {
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
			}))
			if err != nil {
				t.Fatal(err)
			}
			submitCandidate(t, runner, testCandidate("terminal", "https://terminal.example"))
			select {
			case <-terminalCalled:
			case <-time.After(time.Second):
				t.Fatal("terminal not attempted")
			}
			waitFor(t, func() bool { return runner.Stats().Completed == 1 }, "terminal result")
			runner.Close()
			results := drainResults(t, runner)
			if len(results) != 1 || !errors.Is(results[0].Err, test.wantErr) || terminalCalls.Load() != 1 {
				t.Fatalf("results=%+v terminal calls=%d", results, terminalCalls.Load())
			}
		})
	}
}

func TestResultBackpressurePreservesBoundedAccounting(t *testing.T) {
	queue := newFakeQueue()
	runner, err := New(testConfig(2, 2, 1, 2), queue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, runner, testCandidate("one", "https://one.example"))
	submitCandidate(t, runner, testCandidate("two", "https://two.example"))
	waitFor(t, func() bool {
		_, _, terminals, _ := queue.snapshot()
		return terminals == 2
	}, "first terminal transitions")
	// One buffered publication releases a slot; the other worker is blocked on
	// the full result channel, allowing exactly one more candidate to progress.
	submitCandidate(t, runner, testCandidate("three", "https://three.example"))
	waitFor(t, func() bool {
		_, _, terminals, _ := queue.snapshot()
		return terminals == 3
	}, "third terminal transition")
	admissionCtx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if err := runner.Submit(admissionCtx, testCandidate("four", "https://four.example")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("fourth admission error=%v", err)
	}
	stats := runner.Stats()
	if stats.Accepted != 3 || stats.Completed != 1 {
		t.Fatalf("backpressured stats=%+v", stats)
	}
	runner.Close()
	results := drainResults(t, runner)
	if len(results) != 3 {
		t.Fatalf("results=%d", len(results))
	}
	stats = runner.Stats()
	if stats.Accepted != 3 || stats.Completed != 3 || stats.Queued != 0 || stats.InFlight != 0 {
		t.Fatalf("final stats=%+v", stats)
	}
}

func TestMultipleOriginsObeyWorkerAndPerOriginMaxima(t *testing.T) {
	queue := newFakeQueue()
	started := make(chan string, 8)
	release := make(chan struct{}, 8)
	var mu sync.Mutex
	activeTotal := 0
	maxTotal := 0
	activeByOrigin := make(map[string]int)
	maxByOrigin := make(map[string]int)
	executor := ExecutorFunc(func(
		_ context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		origin := job.Sitemap.SitemapURL
		mu.Lock()
		activeTotal++
		activeByOrigin[origin]++
		if activeTotal > maxTotal {
			maxTotal = activeTotal
		}
		if activeByOrigin[origin] > maxByOrigin[origin] {
			maxByOrigin[origin] = activeByOrigin[origin]
		}
		mu.Unlock()
		started <- origin
		<-release
		mu.Lock()
		activeTotal--
		activeByOrigin[origin]--
		mu.Unlock()
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	})
	runner, err := New(testConfig(4, 8, 8, 2), queue, executor)
	if err != nil {
		t.Fatal(err)
	}
	for index := range 4 {
		submitCandidate(t, runner, testCandidate(fmt.Sprintf("a-%d", index), "https://a.example"))
	}
	for index := range 4 {
		submitCandidate(t, runner, testCandidate(fmt.Sprintf("b-%d", index), "https://b.example"))
	}
	firstWave := make(map[string]int)
	for range 4 {
		select {
		case origin := <-started:
			firstWave[origin]++
		case <-time.After(time.Second):
			t.Fatal("first origin-fair wave did not start")
		}
	}
	if firstWave["https://a.example/sitemap.xml"] != 2 ||
		firstWave["https://b.example/sitemap.xml"] != 2 {
		t.Fatalf("first wave=%v", firstWave)
	}
	for range 4 {
		release <- struct{}{}
	}
	for range 4 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("second origin-fair wave did not start")
		}
	}
	for range 4 {
		release <- struct{}{}
	}
	waitFor(t, func() bool {
		_, _, terminals, inflight := queue.snapshot()
		return terminals == 8 && inflight == 0
	}, "all multi-origin terminals")
	runner.Close()
	results := drainResults(t, runner)
	mu.Lock()
	defer mu.Unlock()
	if len(results) != 8 || maxTotal != 4 ||
		maxByOrigin["https://a.example/sitemap.xml"] > 2 ||
		maxByOrigin["https://b.example/sitemap.xml"] > 2 {
		t.Fatalf("results=%d max_total=%d max_by_origin=%v", len(results), maxTotal, maxByOrigin)
	}
}

func TestLoopbackHTTPCompositionPreservesOverlapResultsAndTerminals(t *testing.T) {
	type requestStart struct {
		origin string
	}
	started := make(chan requestStart, 6)
	release := make(chan struct{})
	var concurrencyMu sync.Mutex
	activeTotal := 0
	maxTotal := 0
	activeByOrigin := make(map[string]int)
	maxByOrigin := make(map[string]int)

	newOrigin := func(origin, resultURL string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			concurrencyMu.Lock()
			activeTotal++
			activeByOrigin[origin]++
			if activeTotal > maxTotal {
				maxTotal = activeTotal
			}
			if activeByOrigin[origin] > maxByOrigin[origin] {
				maxByOrigin[origin] = activeByOrigin[origin]
			}
			concurrencyMu.Unlock()
			started <- requestStart{origin: origin}
			<-release
			concurrencyMu.Lock()
			activeTotal--
			activeByOrigin[origin]--
			concurrencyMu.Unlock()
			w.Header().Set("Content-Type", "application/xml")
			_, _ = fmt.Fprintf(w, `<?xml version="1.0"?><urlset><url><loc>%s</loc></url></urlset>`, resultURL)
		}))
	}
	aResultURL := "https://jobs.example/a"
	bResultURL := "https://jobs.example/b"
	aServer := newOrigin("a", aResultURL)
	defer aServer.Close()
	bServer := newOrigin("b", bResultURL)
	defer bServer.Close()
	defer close(release)

	httpClient, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      4_096,
		MaxRequests:              1,
		MaxAggregateDecodedBytes: 4_096,
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          3,
			MaxIdleConnsPerHost:   2,
			MaxConnsPerHost:       2,
			MaxConnections:        3,
			MaxConcurrentRequests: 3,
			IdleConnTimeout:       time.Second,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer httpClient.Close()
	queue := newFakeQueue()
	executor := ExecutorFunc(func(
		ctx context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		sitemapRunner, newErr := sitemap.New(httpClient, job.Sitemap)
		if newErr != nil {
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, newErr
		}
		result, runErr := sitemapRunner.Run(ctx)
		return result, Disposition{Kind: DispositionComplete}, runErr
	})
	runner, err := New(testConfig(3, 6, 6, 2), queue, executor)
	if err != nil {
		t.Fatal(err)
	}
	makeCandidate := func(id, origin string) Candidate {
		candidate := testCandidate(id, origin)
		candidate.Job.Sitemap.SitemapURL = origin + "/" + id + ".xml"
		candidate.Job.Sitemap.RootMaxAttempts = 1
		return candidate
	}
	for index := range 4 {
		submitCandidate(t, runner, makeCandidate(fmt.Sprintf("a-%d", index), aServer.URL))
	}
	for index := range 2 {
		submitCandidate(t, runner, makeCandidate(fmt.Sprintf("b-%d", index), bServer.URL))
	}
	firstWave := make(map[string]int)
	for range 3 {
		select {
		case event := <-started:
			firstWave[event.origin]++
		case <-time.After(time.Second):
			t.Fatal("three loopback requests did not overlap")
		}
	}
	if firstWave["a"] != 2 || firstWave["b"] != 1 {
		t.Fatalf("first HTTP wave=%v", firstWave)
	}
	claims, _, _, inflight := queue.snapshot()
	if claims != 3 || inflight != 3 {
		t.Fatalf("blocked HTTP wave claims=%d inflight=%d", claims, inflight)
	}
	for range 3 {
		release <- struct{}{}
	}
	for range 3 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("second loopback request wave did not start")
		}
	}
	for range 3 {
		release <- struct{}{}
	}
	waitFor(t, func() bool {
		claims, _, terminals, inflight := queue.snapshot()
		return claims == 6 && terminals == 6 && inflight == 0
	}, "loopback terminal accounting")
	runner.Close()
	results := drainResults(t, runner)
	resultURLs := make(map[string]int)
	for _, result := range results {
		if result.Err != nil || len(result.Sitemap.URLs) != 1 {
			t.Fatalf("loopback result=%+v", result)
		}
		resultURLs[result.Sitemap.URLs[0]]++
	}
	concurrencyMu.Lock()
	defer concurrencyMu.Unlock()
	if len(results) != 6 || resultURLs[aResultURL] != 4 || resultURLs[bResultURL] != 2 ||
		maxTotal != 3 || maxByOrigin["a"] != 2 || maxByOrigin["b"] > 2 {
		t.Fatalf(
			"results=%d urls=%v max_total=%d max_by_origin=%v",
			len(results), resultURLs, maxTotal, maxByOrigin,
		)
	}
	stats := runner.Stats()
	if stats.Accepted != 6 || stats.Completed != 6 || stats.MaxInFlight != 3 {
		t.Fatalf("loopback stats=%+v", stats)
	}
}

func TestSeparateRunnerSaturationCannotConsumeAnotherLane(t *testing.T) {
	httpQueue := newFakeQueue()
	browserQueue := newFakeQueue()
	httpStarted := make(chan struct{})
	releaseHTTP := make(chan struct{})
	httpRunner, err := New(testConfig(1, 1, 1, 1), httpQueue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		close(httpStarted)
		<-releaseHTTP
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	browserStarted := make(chan struct{})
	browserRunner, err := New(testConfig(1, 1, 1, 1), browserQueue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		close(browserStarted)
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	submitCandidate(t, httpRunner, testCandidate("http", "https://http.example"))
	select {
	case <-httpStarted:
	case <-time.After(time.Second):
		t.Fatal("HTTP lane did not saturate")
	}
	submitCandidate(t, browserRunner, testCandidate("browser", "https://browser.example"))
	select {
	case <-browserStarted:
	case <-time.After(time.Second):
		t.Fatal("separate browser lane was blocked by HTTP saturation")
	}
	waitFor(t, func() bool {
		_, _, terminals, _ := browserQueue.snapshot()
		return terminals == 1
	}, "browser terminal")
	browserRunner.Close()
	if results := drainResults(t, browserRunner); len(results) != 1 || results[0].Err != nil {
		t.Fatalf("browser results=%+v", results)
	}
	close(releaseHTTP)
	waitFor(t, func() bool {
		_, _, terminals, _ := httpQueue.snapshot()
		return terminals == 1
	}, "HTTP terminal")
	httpRunner.Close()
	if results := drainResults(t, httpRunner); len(results) != 1 || results[0].Err != nil {
		t.Fatalf("HTTP results=%+v", results)
	}
}

func TestCloseGraceDrainsOrCancelsWithoutClaimingQueuedCandidates(t *testing.T) {
	t.Run("started work drains during grace", func(t *testing.T) {
		queue := newFakeQueue()
		started := make(chan struct{})
		release := make(chan struct{})
		canceled := make(chan error, 1)
		config := testConfig(1, 2, 2, 1)
		config.Pool.ShutdownGrace = 500 * time.Millisecond
		runner, err := New(config, queue, ExecutorFunc(func(
			ctx context.Context,
			_ worker.Job,
			_ queuev2.LeaseFence,
		) (sitemap.Result, Disposition, error) {
			close(started)
			select {
			case <-release:
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
			case <-ctx.Done():
				canceled <- context.Cause(ctx)
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
			}
		}))
		if err != nil {
			t.Fatal(err)
		}
		submitCandidate(t, runner, testCandidate("active", "https://active.example"))
		submitCandidate(t, runner, testCandidate("queued", "https://queued.example"))
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("active executor did not start")
		}
		runner.Close()
		select {
		case cause := <-canceled:
			t.Fatalf("soft Close canceled active executor: %v", cause)
		default:
		}
		close(release)
		shutdownCtx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if err := runner.Shutdown(shutdownCtx); err != nil {
			t.Fatal(err)
		}
		results := drainResults(t, runner)
		claims, _, terminals, inflight := queue.snapshot()
		if claims != 1 || terminals != 1 || inflight != 0 || len(results) != 2 {
			t.Fatalf("claims=%d terminals=%d inflight=%d results=%+v", claims, terminals, inflight, results)
		}
		closed := 0
		for _, result := range results {
			if errors.Is(result.Err, ErrClosedBeforeClaim) {
				closed++
			}
		}
		if closed != 1 {
			t.Fatalf("queued close results=%+v", results)
		}
	})

	t.Run("grace expiry cancels active work", func(t *testing.T) {
		queue := newFakeQueue()
		started := make(chan struct{})
		canceled := make(chan error, 1)
		config := testConfig(1, 2, 2, 1)
		config.Pool.ShutdownGrace = 30 * time.Millisecond
		runner, err := New(config, queue, ExecutorFunc(func(
			ctx context.Context,
			_ worker.Job,
			_ queuev2.LeaseFence,
		) (sitemap.Result, Disposition, error) {
			close(started)
			<-ctx.Done()
			canceled <- context.Cause(ctx)
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, ctx.Err()
		}))
		if err != nil {
			t.Fatal(err)
		}
		submitCandidate(t, runner, testCandidate("active", "https://active.example"))
		submitCandidate(t, runner, testCandidate("queued", "https://queued.example"))
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("active executor did not start")
		}
		runner.Close()
		select {
		case cause := <-canceled:
			t.Fatalf("soft Close canceled active executor: %v", cause)
		default:
		}
		select {
		case cause := <-canceled:
			if !errors.Is(cause, context.Canceled) {
				t.Fatalf("grace cancellation cause=%v", cause)
			}
		case <-time.After(time.Second):
			t.Fatal("grace expiry did not cancel active executor")
		}
		shutdownCtx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if err := runner.Shutdown(shutdownCtx); err != nil {
			t.Fatal(err)
		}
		results := drainResults(t, runner)
		claims, _, terminals, inflight := queue.snapshot()
		if claims != 1 || terminals != 0 || inflight != 1 || len(results) != 2 {
			t.Fatalf("claims=%d terminals=%d inflight=%d results=%+v", claims, terminals, inflight, results)
		}
		closed, canceledResult := 0, 0
		for _, result := range results {
			if errors.Is(result.Err, ErrClosedBeforeClaim) {
				closed++
			}
			if errors.Is(result.Err, context.Canceled) {
				canceledResult++
			}
		}
		if closed != 1 || canceledResult != 1 {
			t.Fatalf("grace results=%+v", results)
		}
	})
}

func TestCandidateClientErrorsAreSanitized(t *testing.T) {
	const secret = "redis://queue-user:super-secret-password@redis.example/15"
	for _, operationWithError := range []string{"claim", "complete"} {
		t.Run(operationWithError, func(t *testing.T) {
			clientCalled := make(chan struct{}, 1)
			client := clientFunc(func(
				_ context.Context,
				operation queuev2.RedisCandidateOperation,
			) (queuev2.RedisTransition, error) {
				if operation.Kind == operationWithError {
					clientCalled <- struct{}{}
					return queuev2.RedisTransition{}, errors.New(secret)
				}
				if operation.Kind == "claim" {
					return acceptedClaim("7:1", operation.LeaseTTLMS), nil
				}
				if operation.Kind == "heartbeat" {
					serverTime := operation.PreviousLeaseUntil - 1
					return queuev2.RedisTransition{
						Decision: queuev2.RedisAccepted, Reason: "lease_extended",
						ServerTimeMS: serverTime, ClaimToken: operation.ClaimToken,
						Value: serverTime + operation.LeaseTTLMS, HasValue: true,
					}, nil
				}
				return queuev2.RedisTransition{}, fmt.Errorf("unexpected operation %q", operation.Kind)
			})
			runner, err := New(testConfig(1, 1, 1, 1), client, ExecutorFunc(func(
				context.Context, worker.Job, queuev2.LeaseFence,
			) (sitemap.Result, Disposition, error) {
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
			}))
			if err != nil {
				t.Fatal(err)
			}
			submitCandidate(t, runner, testCandidate("secret", "https://secret.example"))
			select {
			case <-clientCalled:
			case <-time.After(time.Second):
				t.Fatalf("%s client call did not happen", operationWithError)
			}
			waitFor(t, func() bool { return runner.Stats().Completed == 1 }, "sanitized result")
			runner.Close()
			results := drainResults(t, runner)
			wantErr := ErrClaimAmbiguous
			if operationWithError == "complete" {
				wantErr = ErrTerminalAmbiguous
			}
			if len(results) != 1 || !errors.Is(results[0].Err, wantErr) ||
				strings.Contains(results[0].Err.Error(), "super-secret-password") ||
				strings.Contains(results[0].Err.Error(), "redis.example") {
				t.Fatalf("unsanitized result=%+v", results)
			}
		})
	}
}

func TestShutdownNilDoesNotCloseRunner(t *testing.T) {
	queue := newFakeQueue()
	runner, err := New(testConfig(1, 1, 1, 1), queue, ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	if err := runner.Shutdown(nil); !errors.Is(err, worker.ErrInvalidJob) {
		t.Fatalf("nil shutdown error=%v", err)
	}
	submitCandidate(t, runner, testCandidate("still-open", "https://open.example"))
	waitFor(t, func() bool {
		_, _, terminals, _ := queue.snapshot()
		return terminals == 1
	}, "post-nil-shutdown terminal")
	runner.Close()
	if results := drainResults(t, runner); len(results) != 1 || results[0].Err != nil {
		t.Fatalf("results=%+v", results)
	}
}
