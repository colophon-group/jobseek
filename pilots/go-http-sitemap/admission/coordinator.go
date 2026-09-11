package admission

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

var (
	ErrClosed          = errors.New("admission coordinator is closed")
	ErrInvalidContext  = errors.New("invalid admission context")
	ErrInvalidConfig   = errors.New("invalid admission coordinator config")
	ErrFenceMismatch   = errors.New("admission fence mismatch")
	ErrLeaseLost       = errors.New("admission lease lost")
	ErrSupervisorJoin  = errors.New("admission lease supervisor did not join cleanly")
	ErrDependencyPanic = errors.New("admission dependency panicked")
)

// Fence is the complete value-owned identity used at every phase boundary.
// No pointer, slice, or map can be mutated behind the coordinator's back.
type Fence struct {
	// TaskID and BoardID bind the runtime fence to this local dispatch. The
	// remaining fields map one-for-one to runtime-v1 FencingContext. The local
	// string "go" maps only to ENGINE_OWNER_GO; no other enum is admitted.
	TaskID         string
	BoardID        string
	ShardID        string
	RoutingEpoch   uint64
	EngineOwner    string
	ConfigRevision string
	ClaimToken     string
	LeaseID        string
	FenceDigest    [sha256.Size]byte
}

func (fence Fence) Equal(other Fence) bool { return fence == other }

func (fence Fence) validFor(candidate Candidate) bool {
	manifest := candidate.Manifest()
	return fence.TaskID == candidate.TaskID() &&
		fence.BoardID == manifest.BoardID &&
		fence.ShardID != "" &&
		fence.RoutingEpoch > 0 &&
		fence.EngineOwner == "go" &&
		fence.ConfigRevision == manifest.ConfigRevision &&
		fence.ClaimToken != "" &&
		fence.LeaseID != "" &&
		fence.FenceDigest != ([sha256.Size]byte{})
}

// ClaimGrant separates lease timing and the local candidate binding from
// immutable runtime fence identity. RequestStartedAt must be captured
// immediately before the claim request. Together with server time it lets the
// supervisor derive a conservative monotonic deadline without comparing the
// server and worker wall clocks. Heartbeats may replace lease timing without
// changing the Fence echoed by execution and terminal phases.
type ClaimGrant struct {
	Fence            Fence
	CandidateDigest  [sha256.Size]byte
	ServerTimeMS     int64
	LeaseUntilMS     int64
	RequestStartedAt time.Time
}

func (grant ClaimGrant) validFor(candidate Candidate) bool {
	return grant.Fence.validFor(candidate) &&
		grant.CandidateDigest == candidate.Digest() &&
		grant.ServerTimeMS > 0 &&
		grant.LeaseUntilMS > grant.ServerTimeMS &&
		!grant.RequestStartedAt.IsZero()
}

// Claimer is invoked exactly once from worker.Pool's Processor callback. It
// therefore cannot run while the task is merely buffered for a worker or an
// origin slot. Implementations must observe ctx and must not call back into
// Coordinator.Close; Close cancels the context and waits for Claim to return
// so it cannot return ahead of an externally started claim.
type Claimer interface {
	Claim(context.Context, Candidate) (ClaimGrant, error)
}

type ClaimerFunc func(context.Context, Candidate) (ClaimGrant, error)

func (function ClaimerFunc) Claim(ctx context.Context, candidate Candidate) (ClaimGrant, error) {
	return function(ctx, candidate)
}

// LeaseSupervisor starts at most once after a valid claim. It must validate
// the ClaimGrant and derive its initial deadline conservatively from
// RequestStartedAt, ServerTimeMS, and LeaseUntilMS. The handle's Lost context
// closes only for genuine lease loss or supervisor failure, never for a
// normal Stop. Join must return ErrLeaseLost for a lost lease and nil only
// after Stop has fully terminated the supervisor.
type LeaseSupervisor interface {
	Start(context.Context, ClaimGrant) (LeaseHandle, error)
}

type LeaseSupervisorFunc func(context.Context, ClaimGrant) (LeaseHandle, error)

func (function LeaseSupervisorFunc) Start(ctx context.Context, grant ClaimGrant) (LeaseHandle, error) {
	return function(ctx, grant)
}

type LeaseHandle interface {
	Fence() Fence
	Lost() context.Context
	// Stop must be an idempotent, non-blocking signal. All waiting and cleanup
	// belongs in Join, where the coordinator supplies a fixed deadline.
	Stop()
	Join(context.Context) error
}

// Execution echoes the exact input fence. Result is cloned before it crosses
// into Terminaler so returned URL storage cannot be mutated concurrently.
type Execution struct {
	Fence  Fence
	Result sitemap.Result
}

type Executor interface {
	// Execute must observe ctx. Cancellation, timeout, or lease loss must stop
	// origin work before it returns.
	Execute(context.Context, Candidate, Fence) (Execution, error)
}

type ExecutorFunc func(context.Context, Candidate, Fence) (Execution, error)

func (function ExecutorFunc) Execute(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
	return function(ctx, candidate, fence)
}

// Terminaler receives one terminal attempt only after the lease supervisor
// has stopped and joined. It must observe ctx, atomically compare every Fence
// field plus Candidate's exact config revision/fingerprint binding in the
// same authoritative mutation, and echo the exact fence it applied. The echo
// is validation evidence, never separate authorization.
type Terminaler interface {
	Terminal(context.Context, Candidate, Execution, error) (Fence, error)
}

type TerminalerFunc func(context.Context, Candidate, Execution, error) (Fence, error)

func (function TerminalerFunc) Terminal(ctx context.Context, candidate Candidate, execution Execution, executionErr error) (Fence, error) {
	return function(ctx, candidate, execution, executionErr)
}

type Config struct {
	Worker      worker.Config
	JoinTimeout time.Duration
}

// Coordinator owns the worker pool. Its processor adapter is private so
// external callers cannot bypass bounded Submit and worker/origin dispatch.
// It provides no concrete queue, persistence, or retry implementation.
type Coordinator struct {
	claimer     Claimer
	supervisor  LeaseSupervisor
	executor    Executor
	terminaler  Terminaler
	joinTimeout time.Duration

	pool *worker.Pool

	claimGate   sync.RWMutex
	closeCtx    context.Context
	cancelClose context.CancelCauseFunc
	closeOnce   sync.Once
}

type candidateContextKey struct{}

func New(config Config, claimer Claimer, supervisor LeaseSupervisor, executor Executor, terminaler Terminaler) (*Coordinator, error) {
	if config.JoinTimeout <= 0 || config.Worker.Capacity < 0 || claimer == nil || supervisor == nil || executor == nil || terminaler == nil {
		return nil, ErrInvalidConfig
	}
	closeCtx, cancelClose := context.WithCancelCause(context.Background())
	coordinator := &Coordinator{
		claimer:     claimer,
		supervisor:  supervisor,
		executor:    executor,
		terminaler:  terminaler,
		joinTimeout: config.JoinTimeout,
		closeCtx:    closeCtx,
		cancelClose: cancelClose,
	}
	pool, err := worker.NewWithProcessor(config.Worker, coordinatorProcessor{coordinator: coordinator})
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidConfig, err)
	}
	coordinator.pool = pool
	return coordinator, nil
}

// Submit admits an immutable candidate to the bounded worker pool.
// admissionCtx controls only waiting for bounded admission; executionCtx is
// retained for the accepted job's lifetime. Duplicate/stale task rejection is
// owned by the injected claim CAS, not by a second local scheduler.
func (coordinator *Coordinator) Submit(admissionCtx, executionCtx context.Context, candidate Candidate) error {
	if admissionCtx == nil || executionCtx == nil {
		return ErrInvalidContext
	}
	if !candidate.valid() {
		return ErrInvalidCandidate
	}

	select {
	case <-coordinator.closeCtx.Done():
		return ErrClosed
	default:
	}
	jobContext := context.WithValue(executionCtx, candidateContextKey{}, candidate)
	err := coordinator.pool.Submit(admissionCtx, worker.Job{
		ID:      candidate.TaskID(),
		Context: jobContext,
		Sitemap: candidate.Sitemap(),
	})
	if err != nil {
		if errors.Is(err, worker.ErrClosed) {
			return ErrClosed
		}
	}
	return err
}

type coordinatorProcessor struct{ coordinator *Coordinator }

func (processor coordinatorProcessor) Process(ctx context.Context, job worker.Job) (sitemap.Result, error) {
	return processor.coordinator.process(ctx, job)
}

// process is reachable only through coordinatorProcessor after worker.Pool's
// scheduler has assigned both global worker capacity and an origin slot.
func (coordinator *Coordinator) process(ctx context.Context, job worker.Job) (sitemap.Result, error) {
	candidate, ok := ctx.Value(candidateContextKey{}).(Candidate)
	if !ok {
		return sitemap.Result{}, ErrInvalidCandidate
	}
	if candidate.TaskID() != job.ID || candidate.Sitemap() != job.Sitemap {
		return sitemap.Result{}, ErrInvalidCandidate
	}
	if err := ctx.Err(); err != nil {
		return sitemap.Result{}, err
	}

	claimContext, cancelClaim := context.WithCancelCause(ctx)
	stopCloseWatch := context.AfterFunc(coordinator.closeCtx, func() {
		cancelClaim(ErrClosed)
	})
	defer func() {
		stopCloseWatch()
		cancelClaim(nil)
	}()

	// The read reservation spans the external call. Close first cancels
	// claimContext, then takes the exclusive reservation before returning. A
	// conforming Claimer therefore cannot begin after Close has returned and a
	// queued task never crosses this boundary.
	grant, err, claimPanicked := coordinator.claim(claimContext, candidate)
	if claimPanicked {
		return sitemap.Result{}, ErrDependencyPanic
	}
	if err != nil {
		if errors.Is(context.Cause(claimContext), ErrClosed) {
			return sitemap.Result{}, ErrClosed
		}
		return sitemap.Result{}, err
	}
	if errors.Is(context.Cause(claimContext), ErrClosed) {
		return sitemap.Result{}, ErrClosed
	}
	if !grant.validFor(candidate) {
		return sitemap.Result{}, ErrFenceMismatch
	}
	fence := grant.Fence
	if err := ctx.Err(); err != nil {
		return sitemap.Result{}, err
	}

	handle, err, startPanicked := callStart(coordinator.supervisor, ctx, grant)
	if startPanicked {
		return sitemap.Result{}, ErrDependencyPanic
	}
	if err != nil {
		if handle != nil {
			if cleanupErr := coordinator.stopAndJoin(handle); cleanupErr != nil {
				return sitemap.Result{}, fmt.Errorf("%w: %v", ErrSupervisorJoin, cleanupErr)
			}
		}
		return sitemap.Result{}, err
	}
	handleFence, leaseContext, inspectErr := inspectHandle(handle)
	if inspectErr != nil || !handleFence.Equal(fence) || leaseContext == nil {
		if handle != nil {
			if cleanupErr := coordinator.stopAndJoin(handle); cleanupErr != nil {
				return sitemap.Result{}, fmt.Errorf("%w: %v", ErrSupervisorJoin, cleanupErr)
			}
		}
		if errors.Is(inspectErr, ErrDependencyPanic) {
			return sitemap.Result{}, ErrDependencyPanic
		}
		return sitemap.Result{}, ErrFenceMismatch
	}

	executionContext, cancelExecution := context.WithCancelCause(ctx)
	stopLossWatch := context.AfterFunc(leaseContext, func() {
		cancelExecution(ErrLeaseLost)
	})
	if leaseContext.Err() != nil {
		cancelExecution(ErrLeaseLost)
	}
	if cause := context.Cause(executionContext); cause != nil {
		stopLossWatch()
		cancelExecution(nil)
		joinErr := coordinator.stopAndJoin(handle)
		if errors.Is(cause, ErrLeaseLost) || errors.Is(joinErr, ErrLeaseLost) {
			return sitemap.Result{}, ErrLeaseLost
		}
		if joinErr != nil {
			return sitemap.Result{}, fmt.Errorf("%w: %v", ErrSupervisorJoin, joinErr)
		}
		return sitemap.Result{}, cause
	}

	execution, executionErr, executionPanicked := callExecute(coordinator.executor, executionContext, candidate, fence)
	leaseLostBeforeStop := errors.Is(context.Cause(executionContext), ErrLeaseLost) || leaseContext.Err() != nil
	stopLossWatch()
	cancelExecution(nil)

	joinErr := coordinator.stopAndJoin(handle)
	if leaseLostBeforeStop || leaseContext.Err() != nil || errors.Is(joinErr, ErrLeaseLost) {
		return sitemap.Result{}, ErrLeaseLost
	}
	if joinErr != nil {
		return sitemap.Result{}, fmt.Errorf("%w: %v", ErrSupervisorJoin, joinErr)
	}
	if executionPanicked {
		return sitemap.Result{}, ErrDependencyPanic
	}
	if err := ctx.Err(); err != nil {
		return sitemap.Result{}, err
	}
	if !execution.Fence.Equal(fence) {
		return sitemap.Result{}, ErrFenceMismatch
	}

	protectedResult := cloneResult(execution.Result)
	terminalExecution := execution
	terminalExecution.Result = cloneResult(protectedResult)
	echo, terminalErr, terminalPanicked := callTerminal(coordinator.terminaler, ctx, candidate, terminalExecution, executionErr)
	if terminalPanicked {
		return sitemap.Result{}, ErrDependencyPanic
	}
	if !echo.Equal(fence) {
		return sitemap.Result{}, ErrFenceMismatch
	}
	if terminalErr != nil {
		return sitemap.Result{}, terminalErr
	}
	return cloneResult(protectedResult), executionErr
}

func inspectHandle(handle LeaseHandle) (fence Fence, lost context.Context, err error) {
	if handle == nil {
		return Fence{}, nil, ErrFenceMismatch
	}
	defer func() {
		if recover() != nil {
			fence = Fence{}
			lost = nil
			err = ErrDependencyPanic
		}
	}()
	return handle.Fence(), handle.Lost(), nil
}

func (coordinator *Coordinator) claim(ctx context.Context, candidate Candidate) (grant ClaimGrant, err error, panicked bool) {
	coordinator.claimGate.RLock()
	defer coordinator.claimGate.RUnlock()
	if coordinator.closeCtx.Err() != nil {
		return ClaimGrant{}, ErrClosed, false
	}
	return callClaim(coordinator.claimer, ctx, candidate)
}

func callClaim(claimer Claimer, ctx context.Context, candidate Candidate) (grant ClaimGrant, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			grant = ClaimGrant{}
			err = ErrDependencyPanic
			panicked = true
		}
	}()
	grant, err = claimer.Claim(ctx, candidate)
	return grant, err, false
}

func callStart(supervisor LeaseSupervisor, ctx context.Context, grant ClaimGrant) (handle LeaseHandle, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			handle = nil
			err = ErrDependencyPanic
			panicked = true
		}
	}()
	handle, err = supervisor.Start(ctx, grant)
	return handle, err, false
}

func callExecute(executor Executor, ctx context.Context, candidate Candidate, fence Fence) (execution Execution, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			execution = Execution{}
			err = ErrDependencyPanic
			panicked = true
		}
	}()
	execution, err = executor.Execute(ctx, candidate, fence)
	return execution, err, false
}

func callTerminal(terminaler Terminaler, ctx context.Context, candidate Candidate, execution Execution, executionErr error) (fence Fence, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			fence = Fence{}
			err = ErrDependencyPanic
			panicked = true
		}
	}()
	fence, err = terminaler.Terminal(ctx, candidate, execution, executionErr)
	return fence, err, false
}

func (coordinator *Coordinator) stopAndJoin(handle LeaseHandle) error {
	stopErr := callStop(handle)
	joinContext, cancel := context.WithTimeout(context.Background(), coordinator.joinTimeout)
	defer cancel()
	joinErr := callJoin(joinContext, handle)
	if stopErr != nil {
		return stopErr
	}
	return joinErr
}

func callStop(handle LeaseHandle) (err error) {
	defer func() {
		if recover() != nil {
			err = ErrDependencyPanic
		}
	}()
	handle.Stop()
	return nil
}

func callJoin(ctx context.Context, handle LeaseHandle) (err error) {
	defer func() {
		if recover() != nil {
			err = ErrDependencyPanic
		}
	}()
	return handle.Join(ctx)
}

func cloneResult(result sitemap.Result) sitemap.Result {
	result.URLs = append([]string(nil), result.URLs...)
	return result
}

func (coordinator *Coordinator) Results() <-chan worker.Result { return coordinator.pool.Results() }

func (coordinator *Coordinator) Done() <-chan struct{} { return coordinator.pool.Done() }

func (coordinator *Coordinator) Stats() worker.Stats { return coordinator.pool.Stats() }

// Close starts the worker drain and cancels any claim phase, then waits for
// every already-started Claim to return before it returns. Claimer is required
// to observe its context. A violating Claimer can keep Close blocked, just as
// a violating worker Processor can keep Pool.Close from draining; Shutdown
// still respects its caller deadline.
func (coordinator *Coordinator) Close() {
	coordinator.initiateClose()
	coordinator.claimGate.Lock()
	coordinator.claimGate.Unlock()
}

func (coordinator *Coordinator) Shutdown(ctx context.Context) error {
	if ctx == nil {
		return ErrInvalidContext
	}
	coordinator.initiateClose()
	select {
	case <-coordinator.Done():
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (coordinator *Coordinator) initiateClose() {
	coordinator.closeOnce.Do(func() {
		coordinator.cancelClose(ErrClosed)
		coordinator.pool.Close()
	})
}

var _ worker.Processor = coordinatorProcessor{}
