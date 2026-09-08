package queuev2

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

type manualLeaseTicker struct{ ticks chan time.Time }

func (t *manualLeaseTicker) C() <-chan time.Time { return t.ticks }
func (t *manualLeaseTicker) Stop()               {}

type manualLeaseTimer struct {
	ticks  chan time.Time
	resets chan time.Duration
}

func (t *manualLeaseTimer) C() <-chan time.Time { return t.ticks }
func (t *manualLeaseTimer) Stop() bool          { return true }
func (t *manualLeaseTimer) Reset(duration time.Duration) bool {
	t.resets <- duration
	return true
}

type heartbeatResponse struct {
	transition RedisTransition
	err        error
}

type scriptedHeartbeatExecutor struct {
	calls     chan RedisCandidateOperation
	responses chan heartbeatResponse
}

type blockingHeartbeatExecutor struct{}

func (blockingHeartbeatExecutor) Execute(
	ctx context.Context,
	_ RedisCandidateOperation,
) (RedisTransition, error) {
	<-ctx.Done()
	return RedisTransition{}, ctx.Err()
}

func newScriptedHeartbeatExecutor() *scriptedHeartbeatExecutor {
	return &scriptedHeartbeatExecutor{
		calls:     make(chan RedisCandidateOperation, 16),
		responses: make(chan heartbeatResponse, 16),
	}
}

func (e *scriptedHeartbeatExecutor) Execute(
	ctx context.Context,
	operation RedisCandidateOperation,
) (RedisTransition, error) {
	select {
	case e.calls <- operation:
	case <-ctx.Done():
		return RedisTransition{}, ctx.Err()
	}
	select {
	case response := <-e.responses:
		return response.transition, response.err
	case <-ctx.Done():
		return RedisTransition{}, ctx.Err()
	}
}

func testLeaseFence() LeaseFence {
	return LeaseFence{
		TaskID: "board-1",
		Route: Route{
			ShardID:      "sitemap-0",
			RoutingEpoch: 7,
			EngineOwner:  "go",
		},
		ConfigRevision: 11,
		ClaimToken:     "7:31",
	}
}

func testLeaseGrant() LeaseGrant {
	return LeaseGrant{
		Fence:            testLeaseFence(),
		ServerTimeMS:     500,
		LeaseUntilMS:     10_500,
		RequestStartedAt: time.Now(),
	}
}

func testLeaseSupervisor(
	t *testing.T,
	executor HeartbeatExecutor,
) (*LeaseSupervisor, *manualLeaseTicker) {
	t.Helper()
	supervisor, err := NewLeaseSupervisor(executor, LeaseSupervisorConfig{
		HeartbeatInterval: time.Second,
		HeartbeatTimeout:  500 * time.Millisecond,
		LeaseTTLMS:        10_000,
	})
	if err != nil {
		t.Fatal(err)
	}
	ticker := &manualLeaseTicker{ticks: make(chan time.Time, 16)}
	supervisor.newTicker = func(time.Duration) leaseTicker { return ticker }
	return supervisor, ticker
}

func tickHeartbeat(
	t *testing.T,
	ticker *manualLeaseTicker,
	executor *scriptedHeartbeatExecutor,
	response heartbeatResponse,
) RedisCandidateOperation {
	t.Helper()
	ticker.ticks <- time.Now()
	select {
	case operation := <-executor.calls:
		executor.responses <- response
		return operation
	case <-time.After(time.Second):
		t.Fatal("heartbeat was not attempted")
		return RedisCandidateOperation{}
	}
}

func waitClosed(t *testing.T, channel <-chan struct{}, label string) {
	t.Helper()
	select {
	case <-channel:
	case <-time.After(time.Second):
		t.Fatalf("%s did not close", label)
	}
}

func acceptedHeartbeat(token string, serverTimeMS, leaseUntilMS int64) RedisTransition {
	return RedisTransition{
		Decision:     RedisAccepted,
		Reason:       "lease_extended",
		ServerTimeMS: serverTimeMS,
		ClaimToken:   token,
		Value:        leaseUntilMS,
		HasValue:     true,
	}
}

func TestLeaseSupervisorExtendsAcceptedHeartbeatWithoutCancellation(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, ticker := testLeaseSupervisor(t, executor)
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	operation := tickHeartbeat(t, ticker, executor, heartbeatResponse{
		transition: acceptedHeartbeat("7:31", 900, 10_900),
	})
	if operation.PreviousLeaseUntil != 10_500 || operation.LeaseTTLMS != 10_000 {
		t.Fatalf("unexpected heartbeat operation: %+v", operation)
	}
	for deadline := time.Now().Add(time.Second); handle.LeaseUntilMS() != 10_900; {
		if time.Now().After(deadline) {
			t.Fatalf("lease deadline was not extended: %d", handle.LeaseUntilMS())
		}
		time.Sleep(time.Millisecond)
	}
	if handle.Loss() != nil || context.Cause(handle.Context()) != nil {
		t.Fatalf("accepted heartbeat canceled current lease: %v", context.Cause(handle.Context()))
	}
	handle.Stop()
}

func TestLeaseSupervisorResetsLocalDeadlineAfterAcceptedHeartbeat(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, ticker := testLeaseSupervisor(t, executor)
	timer := &manualLeaseTimer{
		ticks:  make(chan time.Time, 1),
		resets: make(chan time.Duration, 1),
	}
	supervisor.newTimer = func(time.Duration) leaseTimer { return timer }
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	_ = tickHeartbeat(t, ticker, executor, heartbeatResponse{
		transition: acceptedHeartbeat("7:31", 900, 10_900),
	})
	select {
	case duration := <-timer.resets:
		if duration <= 0 || duration > 10*time.Second {
			t.Fatalf("unexpected reset duration: %s", duration)
		}
	case <-time.After(time.Second):
		t.Fatal("accepted heartbeat did not reset the local deadline timer")
	}
	if handle.Loss() != nil || context.Cause(handle.Context()) != nil {
		t.Fatalf("timer reset canceled current work: %v", context.Cause(handle.Context()))
	}
	timer.ticks <- time.Now()
	waitClosed(t, handle.Lost(), "reset local deadline")
	if loss := handle.Loss(); !errors.Is(loss, ErrLeaseDeadlineElapsed) {
		t.Fatalf("reset timer did not retain deadline semantics: %v", loss)
	}
	handle.Stop()
}

func TestLeaseSupervisorCancelsWorkWithFirstTypedLoss(t *testing.T) {
	cases := []struct {
		name       string
		response   heartbeatResponse
		decision   RedisDecision
		reason     string
		unwrapsErr error
	}{
		{
			name: "fenced",
			response: heartbeatResponse{transition: RedisTransition{
				Decision: RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 900,
			}},
			decision: RedisFenced, reason: "claim_token_mismatch",
		},
		{
			name: "not current",
			response: heartbeatResponse{transition: RedisTransition{
				Decision: RedisNotCurrent, Reason: "lease_expired", ServerTimeMS: 900,
			}},
			decision: RedisNotCurrent, reason: "lease_expired",
		},
		{
			name: "redis time invalid",
			response: heartbeatResponse{transition: RedisTransition{
				Decision: RedisNotCurrent, Reason: "redis_time_invalid",
			}},
			decision: RedisNotCurrent, reason: "redis_time_invalid",
		},
		{
			name: "transport transition",
			response: heartbeatResponse{transition: RedisTransition{
				Decision: RedisTransportError, Reason: "invalid_redis_reply",
			}},
			decision: RedisTransportError, reason: "invalid_redis_reply",
		},
		{
			name:       "client error",
			response:   heartbeatResponse{err: context.DeadlineExceeded},
			decision:   RedisTransportError,
			reason:     "heartbeat_client_error",
			unwrapsErr: context.DeadlineExceeded,
		},
		{
			name: "malformed accepted transition",
			response: heartbeatResponse{transition: RedisTransition{
				Decision: RedisAccepted, Reason: "lease_extended", ServerTimeMS: 900,
				ClaimToken: "7:31", Value: 1_000, HasValue: true,
			}},
			decision:   RedisTransportError,
			reason:     "invalid_heartbeat_transition",
			unwrapsErr: ErrInvalidHeartbeatTransition,
		},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			executor := newScriptedHeartbeatExecutor()
			supervisor, ticker := testLeaseSupervisor(t, executor)
			handle, err := supervisor.Start(context.Background(), testLeaseGrant())
			if err != nil {
				t.Fatal(err)
			}
			_ = tickHeartbeat(t, ticker, executor, test.response)
			waitClosed(t, handle.Lost(), "lost")
			select {
			case <-handle.Context().Done():
			default:
				t.Fatal("Lost was published before work cancellation")
			}

			loss := handle.Loss()
			transition := loss.Transition()
			if loss == nil || transition.Decision != test.decision ||
				transition.Reason != test.reason {
				t.Fatalf("unexpected typed loss: %+v", loss)
			}
			if context.Cause(handle.Context()) != loss {
				t.Fatalf("work context did not preserve first loss: %v", context.Cause(handle.Context()))
			}
			if test.unwrapsErr != nil && !errors.Is(loss, test.unwrapsErr) {
				t.Fatalf("loss does not unwrap %v: %v", test.unwrapsErr, loss)
			}
			handle.Stop()
			handle.Stop()
		})
	}
}

func TestLeaseSupervisorRejectsImpossibleAcceptedRenewals(t *testing.T) {
	cases := []RedisTransition{
		acceptedHeartbeat("7:31", 900, 10_899),
		acceptedHeartbeat("7:31", 10_500, 20_500),
	}
	for _, transition := range cases {
		executor := newScriptedHeartbeatExecutor()
		supervisor, ticker := testLeaseSupervisor(t, executor)
		handle, err := supervisor.Start(context.Background(), testLeaseGrant())
		if err != nil {
			t.Fatal(err)
		}
		_ = tickHeartbeat(t, ticker, executor, heartbeatResponse{transition: transition})
		waitClosed(t, handle.Lost(), "loss for impossible renewal")
		loss := handle.Loss()
		if lossTransition := loss.Transition(); loss == nil ||
			lossTransition.Decision != RedisTransportError ||
			lossTransition.Reason != "invalid_heartbeat_transition" ||
			!errors.Is(loss, ErrInvalidHeartbeatTransition) {
			t.Fatalf("impossible renewal did not fail closed: %+v -> %v", transition, loss)
		}
		handle.Stop()
	}
}

func TestLeaseSupervisorRejectsMalformedLossTransitions(t *testing.T) {
	cases := []RedisTransition{
		{Decision: RedisDecision("unknown"), Reason: "lease_expired", ServerTimeMS: 900},
		{Decision: RedisFenced, Reason: "lease_expired", ServerTimeMS: 900},
		{Decision: RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 0},
		{Decision: RedisNotCurrent, Reason: "unknown", ServerTimeMS: 900},
		{Decision: RedisNotCurrent, Reason: "redis_time_invalid", ServerTimeMS: 900},
		{Decision: RedisTransportError, Reason: "unknown"},
		{Decision: RedisTransportError, Reason: "redis_error", ServerTimeMS: 900},
		{Decision: RedisNotCurrent, Reason: "lease_expired", ServerTimeMS: 900, HasValue: true},
		{Decision: RedisNotCurrent, Reason: "lease_expired", ServerTimeMS: 900, Value: 123},
		{Decision: RedisNotCurrent, Reason: "lease_expired", ServerTimeMS: 900, ClaimToken: "7:31"},
	}
	for _, transition := range cases {
		executor := newScriptedHeartbeatExecutor()
		supervisor, ticker := testLeaseSupervisor(t, executor)
		handle, err := supervisor.Start(context.Background(), testLeaseGrant())
		if err != nil {
			t.Fatal(err)
		}
		_ = tickHeartbeat(t, ticker, executor, heartbeatResponse{transition: transition})
		waitClosed(t, handle.Lost(), "lost")
		loss := handle.Loss()
		lossTransition := loss.Transition()
		if loss == nil ||
			lossTransition.Decision != RedisTransportError ||
			lossTransition.Reason != "invalid_heartbeat_transition" ||
			!errors.Is(loss, ErrInvalidHeartbeatTransition) {
			t.Fatalf("malformed transition did not fail closed: %+v -> %v", transition, loss)
		}
		handle.Stop()
	}
}

func TestLeaseSupervisorParentCancellationIsNotLeaseLoss(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, _ := testLeaseSupervisor(t, executor)
	parent, cancel := context.WithCancelCause(context.Background())
	handle, err := supervisor.Start(parent, testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	parentCause := errors.New("bounded shutdown")
	cancel(parentCause)
	waitClosed(t, handle.Context().Done(), "work context")
	handle.Stop()
	if handle.Loss() != nil {
		t.Fatalf("parent cancellation manufactured lease loss: %v", handle.Loss())
	}
	if context.Cause(handle.Context()) != parentCause {
		t.Fatalf("parent cause was not preserved: %v", context.Cause(handle.Context()))
	}
}

func TestLeaseSupervisorImmediateStopPreservesCompletedParentCause(t *testing.T) {
	for range 100 {
		executor := newScriptedHeartbeatExecutor()
		supervisor, _ := testLeaseSupervisor(t, executor)
		parent, cancel := context.WithCancelCause(context.Background())
		handle, err := supervisor.Start(parent, testLeaseGrant())
		if err != nil {
			t.Fatal(err)
		}
		parentCause := errors.New("parent completed first")
		cancel(parentCause)
		handle.Stop()
		if handle.Loss() != nil || context.Cause(handle.Context()) != parentCause {
			t.Fatalf("completed parent lost linearization: loss=%v cause=%v", handle.Loss(), context.Cause(handle.Context()))
		}
	}
}

func TestLeaseSupervisorRejectsAlreadyCanceledParent(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, _ := testLeaseSupervisor(t, executor)
	parent, cancel := context.WithCancelCause(context.Background())
	parentCause := errors.New("already canceled")
	cancel(parentCause)
	if _, err := supervisor.Start(parent, testLeaseGrant()); err != parentCause {
		t.Fatalf("already-canceled parent returned %v", err)
	}
}

func TestLeaseSupervisorPreservesParentValuesAndDeadline(t *testing.T) {
	type contextKey string
	const key contextKey = "lease-parent-value"
	base := context.WithValue(context.Background(), key, "retained")
	parent, cancel := context.WithTimeout(base, time.Hour)
	defer cancel()
	parentDeadline, ok := parent.Deadline()
	if !ok {
		t.Fatal("test parent has no deadline")
	}
	executor := newScriptedHeartbeatExecutor()
	supervisor, _ := testLeaseSupervisor(t, executor)
	handle, err := supervisor.Start(parent, testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	workDeadline, ok := handle.Context().Deadline()
	if !ok || !workDeadline.Equal(parentDeadline) {
		t.Fatalf("parent deadline was not preserved: got %v, %t; want %v", workDeadline, ok, parentDeadline)
	}
	if value := handle.Context().Value(key); value != "retained" {
		t.Fatalf("parent value was not preserved: %v", value)
	}
	handle.Stop()
}

func TestLeaseSupervisorHeartbeatTimeoutCancelsWork(t *testing.T) {
	supervisor, ticker := testLeaseSupervisor(t, blockingHeartbeatExecutor{})
	supervisor.config.HeartbeatTimeout = 5 * time.Millisecond
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	ticker.ticks <- time.Now()
	waitClosed(t, handle.Lost(), "lost")
	loss := handle.Loss()
	if loss == nil || !errors.Is(loss, context.DeadlineExceeded) {
		t.Fatalf("heartbeat timeout was not preserved: %v", loss)
	}
	handle.Stop()
}

func TestLeaseHandlePreservesFirstLossExactlyOnce(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, _ := testLeaseSupervisor(t, executor)
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	first := newLeaseLostError(RedisTransition{
		Decision: RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 900,
	}, nil)
	second := newLeaseLostError(RedisTransition{
		Decision: RedisNotCurrent, Reason: "lease_expired", ServerTimeMS: 901,
	}, nil)
	handle.markLost(first)
	handle.markLost(second)
	if handle.Loss() != first || context.Cause(handle.Context()) != first {
		t.Fatalf("first loss was replaced: %v", handle.Loss())
	}
	handle.Stop()
}

func TestLeaseSupervisorLossDuringWorkCancelsAndSuppressesMutation(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, ticker := testLeaseSupervisor(t, executor)
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	var mutations atomic.Int64
	workDone := make(chan struct{})
	go func() {
		defer close(workDone)
		<-handle.Context().Done()
		if context.Cause(handle.Context()) == nil {
			mutations.Add(1)
		}
	}()

	_ = tickHeartbeat(t, ticker, executor, heartbeatResponse{transition: RedisTransition{
		Decision: RedisFenced, Reason: "routing_epoch_mismatch", ServerTimeMS: 900,
	}})
	waitClosed(t, workDone, "work")
	handle.Stop()
	if mutations.Load() != 0 {
		t.Fatal("lease-lost work mutated its sink")
	}
}

type fakeTransactionalSink struct {
	mu      sync.Mutex
	current LeaseFence
	writes  int
}

func (s *fakeTransactionalSink) rotate(next LeaseFence) {
	s.mu.Lock()
	s.current = next
	s.mu.Unlock()
}

func (s *fakeTransactionalSink) commit(fence LeaseFence) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.current != fence {
		return false
	}
	s.writes++
	return true
}

func (s *fakeTransactionalSink) writeCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.writes
}

func TestFakeTransactionalSinkRejectsFenceRotationBeforeCommit(t *testing.T) {
	original := testLeaseFence()
	mutations := map[string]func(*LeaseFence){
		"task":     func(f *LeaseFence) { f.TaskID = "board-2" },
		"shard":    func(f *LeaseFence) { f.Route.ShardID = "sitemap-1" },
		"epoch":    func(f *LeaseFence) { f.Route.RoutingEpoch++ },
		"owner":    func(f *LeaseFence) { f.Route.EngineOwner = "python" },
		"revision": func(f *LeaseFence) { f.ConfigRevision++ },
		"token":    func(f *LeaseFence) { f.ClaimToken = "7:32" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			sink := &fakeTransactionalSink{current: original}
			if !sink.commit(original) {
				t.Fatal("current full fence was rejected")
			}
			rotated := original
			mutate(&rotated)
			sink.rotate(rotated)
			if sink.commit(original) {
				t.Fatal("stale full fence mutated after atomic rotation")
			}
			if sink.writeCount() != 1 {
				t.Fatalf("unexpected committed mutation count: %d", sink.writeCount())
			}
		})
	}
}

func startBlockedHeartbeat(
	t *testing.T,
) (*LeaseHandle, *scriptedHeartbeatExecutor) {
	t.Helper()
	executor := newScriptedHeartbeatExecutor()
	supervisor, ticker := testLeaseSupervisor(t, executor)
	handle, err := supervisor.Start(context.Background(), testLeaseGrant())
	if err != nil {
		t.Fatal(err)
	}
	ticker.ticks <- time.Now()
	select {
	case <-executor.calls:
	case <-time.After(time.Second):
		t.Fatal("heartbeat was not attempted")
	}
	return handle, executor
}

func TestLeaseSupervisorStopLinearizesBeforePendingLoss(t *testing.T) {
	handle, _ := startBlockedHeartbeat(t)
	handle.Stop()
	if handle.Loss() != nil {
		t.Fatalf("Stop winner recorded a lease loss: %v", handle.Loss())
	}
	if context.Cause(handle.Context()) != context.Canceled {
		t.Fatalf("Stop winner lost cancellation cause: %v", context.Cause(handle.Context()))
	}
	select {
	case <-handle.Lost():
		t.Fatal("Stop winner published Lost")
	default:
	}
}

func TestLeaseSupervisorLossLinearizesBeforeStop(t *testing.T) {
	handle, executor := startBlockedHeartbeat(t)
	executor.responses <- heartbeatResponse{transition: RedisTransition{
		Decision: RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 900,
	}}
	waitClosed(t, handle.Lost(), "loss winner")
	loss := handle.Loss()
	handle.Stop()
	if loss == nil || handle.Loss() != loss || context.Cause(handle.Context()) != loss {
		t.Fatalf("loss winner was not retained: loss=%v cause=%v", loss, context.Cause(handle.Context()))
	}
}

func TestLeaseSupervisorParentAndLossLinearization(t *testing.T) {
	t.Run("parent wins", func(t *testing.T) {
		executor := newScriptedHeartbeatExecutor()
		supervisor, ticker := testLeaseSupervisor(t, executor)
		parent, cancelParent := context.WithCancelCause(context.Background())
		handle, err := supervisor.Start(parent, testLeaseGrant())
		if err != nil {
			t.Fatal(err)
		}
		ticker.ticks <- time.Now()
		select {
		case <-executor.calls:
		case <-time.After(time.Second):
			t.Fatal("heartbeat was not attempted")
		}
		parentCause := errors.New("parent won")
		cancelParent(parentCause)
		waitClosed(t, handle.Context().Done(), "parent cancellation")
		handle.Stop()
		if handle.Loss() != nil || context.Cause(handle.Context()) != parentCause {
			t.Fatalf("parent winner was inconsistent: loss=%v cause=%v", handle.Loss(), context.Cause(handle.Context()))
		}
	})

	t.Run("loss wins", func(t *testing.T) {
		executor := newScriptedHeartbeatExecutor()
		supervisor, ticker := testLeaseSupervisor(t, executor)
		parent, cancelParent := context.WithCancelCause(context.Background())
		handle, err := supervisor.Start(parent, testLeaseGrant())
		if err != nil {
			t.Fatal(err)
		}
		_ = tickHeartbeat(t, ticker, executor, heartbeatResponse{transition: RedisTransition{
			Decision: RedisFenced, Reason: "claim_token_mismatch", ServerTimeMS: 900,
		}})
		waitClosed(t, handle.Lost(), "loss cancellation")
		loss := handle.Loss()
		cancelParent(errors.New("late parent"))
		handle.Stop()
		if loss == nil || handle.Loss() != loss || context.Cause(handle.Context()) != loss {
			t.Fatalf("loss winner was inconsistent: loss=%v cause=%v", loss, context.Cause(handle.Context()))
		}
	})
}

func TestLeaseSupervisorCancelsAtConservativeLocalDeadline(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	supervisor, err := NewLeaseSupervisor(executor, LeaseSupervisorConfig{
		HeartbeatInterval: 5 * time.Millisecond,
		HeartbeatTimeout:  5 * time.Millisecond,
		LeaseTTLMS:        25,
	})
	if err != nil {
		t.Fatal(err)
	}
	supervisor.newTicker = func(time.Duration) leaseTicker {
		return &manualLeaseTicker{ticks: make(chan time.Time)}
	}
	grant := testLeaseGrant()
	grant.ServerTimeMS = 500
	grant.LeaseUntilMS = 525
	grant.RequestStartedAt = time.Now()
	handle, err := supervisor.Start(context.Background(), grant)
	if err != nil {
		t.Fatal(err)
	}
	waitClosed(t, handle.Lost(), "local lease deadline")
	loss := handle.Loss()
	if transition := loss.Transition(); loss == nil ||
		transition.Decision != RedisTransportError ||
		transition.Reason != "lease_deadline_elapsed" ||
		!errors.Is(loss, ErrLeaseDeadlineElapsed) {
		t.Fatalf("local deadline did not fail closed: %v", loss)
	}
	handle.Stop()
}

func TestLeaseSupervisorRejectsInvalidInputs(t *testing.T) {
	executor := newScriptedHeartbeatExecutor()
	valid := LeaseSupervisorConfig{
		HeartbeatInterval: time.Second,
		HeartbeatTimeout:  time.Second,
		LeaseTTLMS:        10_000,
	}
	for _, config := range []LeaseSupervisorConfig{
		{},
		{HeartbeatInterval: time.Second, HeartbeatTimeout: time.Second, LeaseTTLMS: 0},
		{HeartbeatInterval: 9 * time.Second, HeartbeatTimeout: time.Second, LeaseTTLMS: 10_000},
		{HeartbeatInterval: time.Second, HeartbeatTimeout: 10 * time.Second, LeaseTTLMS: 10_000},
	} {
		if _, err := NewLeaseSupervisor(executor, config); err == nil {
			t.Fatalf("invalid supervisor config accepted: %+v", config)
		}
	}
	supervisor, err := NewLeaseSupervisor(executor, valid)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := supervisor.Start(context.Background(), LeaseGrant{}); err == nil {
		t.Fatal("invalid fence accepted")
	}
	for _, grant := range []LeaseGrant{
		{Fence: testLeaseFence(), ServerTimeMS: 500, LeaseUntilMS: 10_499, RequestStartedAt: time.Now()},
		{Fence: testLeaseFence(), ServerTimeMS: 500, LeaseUntilMS: 10_500},
		{Fence: testLeaseFence(), ServerTimeMS: 500, LeaseUntilMS: 10_500, RequestStartedAt: time.Now().Add(time.Second)},
		{Fence: testLeaseFence(), ServerTimeMS: 500, LeaseUntilMS: 10_500, RequestStartedAt: time.Now().Add(-11 * time.Second)},
	} {
		if _, err := supervisor.Start(context.Background(), grant); err == nil {
			t.Fatalf("invalid grant accepted: %+v", grant)
		}
	}
}

func TestRealRedisLeaseSupervisorCancelsAfterTokenRotation(t *testing.T) {
	redisURL := os.Getenv("QUEUE_V2_REDIS_URL")
	if redisURL == "" || os.Getenv("QUEUE_V2_REDIS_ISOLATED") != "1" {
		t.Skip("requires QUEUE_V2_REDIS_URL and QUEUE_V2_REDIS_ISOLATED=1")
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	size, err := client.DBSize(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	if size != 0 {
		t.Fatalf("Redis DB must start empty, got %d", size)
	}

	namespace := fmt.Sprintf("supervisor-go-%d", time.Now().UnixNano())
	claimStartedAt := time.Now()
	queue, claim, claimed := prepareFaultClaim(t, ctx, client, namespace)
	defer func() {
		if err := client.Del(context.Background(), queue.Keys()...).Err(); err != nil {
			t.Errorf("clean candidate keys: %v", err)
		}
	}()
	reschedule := claim
	reschedule.Kind = "reschedule"
	reschedule.LeaseTTLMS = 0
	reschedule.RescheduleDelayMS = 0
	if result := executeShared(t, ctx, queue, reschedule); result.Decision != RedisAccepted {
		t.Fatalf("reschedule failed: %+v", result)
	}
	reclaim := claim
	reclaim.ClaimToken = ""
	reclaimed := executeShared(t, ctx, queue, reclaim)
	if reclaimed.Decision != RedisAccepted || reclaimed.ClaimToken == claim.ClaimToken {
		t.Fatalf("reclaim did not rotate token: %+v", reclaimed)
	}

	supervisor, err := NewLeaseSupervisor(queue, LeaseSupervisorConfig{
		HeartbeatInterval: 10 * time.Millisecond,
		HeartbeatTimeout:  time.Second,
		LeaseTTLMS:        5_000,
	})
	if err != nil {
		t.Fatal(err)
	}
	handle, err := supervisor.Start(ctx, LeaseGrant{
		Fence: LeaseFence{
			TaskID:         claim.TaskID,
			Route:          claim.Route,
			ConfigRevision: claim.ConfigRevision,
			ClaimToken:     claim.ClaimToken,
		},
		ServerTimeMS:     claimed.ServerTimeMS,
		LeaseUntilMS:     claimed.Value,
		RequestStartedAt: claimStartedAt,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer handle.Stop()

	waitClosed(t, handle.Lost(), "real Redis token-rotation loss")
	loss := handle.Loss()
	transition := loss.Transition()
	if loss == nil ||
		transition.Decision != RedisFenced ||
		transition.Reason != "claim_token_mismatch" {
		t.Fatalf("unexpected real Redis loss: %v", loss)
	}
	handle.Stop()
	if err := client.Del(ctx, queue.Keys()...).Err(); err != nil {
		t.Fatal(err)
	}
	size, err = client.DBSize(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	if size != 0 {
		t.Fatalf("Redis DB must end empty, got %d", size)
	}
}
