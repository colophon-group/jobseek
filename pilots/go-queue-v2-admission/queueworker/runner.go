package queueworker

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"
	"sync"
	"time"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

var (
	ErrInvalidConfig       = errors.New("invalid queue worker config")
	ErrInvalidCandidate    = errors.New("invalid queue worker candidate")
	ErrInvalidDisposition  = errors.New("invalid queue worker disposition")
	ErrClaimNotAccepted    = errors.New("queue-v2 claim not accepted")
	ErrClaimAmbiguous      = errors.New("queue-v2 claim outcome ambiguous")
	ErrTerminalNotAccepted = errors.New("queue-v2 terminal transition not accepted")
	ErrTerminalAmbiguous   = errors.New("queue-v2 terminal outcome ambiguous")
	ErrClosedBeforeClaim   = errors.New("queue worker closed before claim")
)

// Candidate is an immutable, unclaimed reference submitted to a Runner. Job.ID
// is the queue-v2 task identity. ConfigRevision identifies the exact payload
// revision the injected Executor will process after a successful claim.
type Candidate struct {
	Job            worker.Job
	ConfigRevision int64
}

// DispositionKind is the one queue transition requested after successful
// execution. Failures that should run again use DispositionReschedule.
type DispositionKind string

const (
	DispositionComplete   DispositionKind = "complete"
	DispositionReschedule DispositionKind = "reschedule"
)

// Disposition controls the exact-fence terminal queue transition. Complete
// requires a zero delay. RescheduleDelay must be nonnegative and is rounded up
// to milliseconds so a positive duration can never become an immediate retry.
type Disposition struct {
	Kind            DispositionKind
	RescheduleDelay time.Duration
}

// Executor runs one accepted claim. Implementations must observe ctx and must
// pass fence to every future authoritative write performed during execution.
type Executor interface {
	Execute(context.Context, worker.Job, queuev2.LeaseFence) (sitemap.Result, Disposition, error)
}

type ExecutorFunc func(context.Context, worker.Job, queuev2.LeaseFence) (sitemap.Result, Disposition, error)

func (f ExecutorFunc) Execute(
	ctx context.Context,
	job worker.Job,
	fence queuev2.LeaseFence,
) (sitemap.Result, Disposition, error) {
	return f(ctx, job, fence)
}

// CandidateClient is the single queue-v2 operation surface used for claims,
// heartbeats, and terminal CAS. queuev2.RedisCandidateClient satisfies it.
type CandidateClient interface {
	Execute(context.Context, queuev2.RedisCandidateOperation) (queuev2.RedisTransition, error)
}

var _ CandidateClient = (*queuev2.RedisCandidateClient)(nil)

// Config fixes every bound and routing decision for a Runner. Route and
// LeaseTTLMS are copied at construction and cannot vary by submission.
type Config struct {
	Pool              worker.Config
	Route             queuev2.Route
	LeaseTTLMS        int64
	HeartbeatInterval time.Duration
	HeartbeatTimeout  time.Duration
	TerminalTimeout   time.Duration
}

// Runner admits unclaimed candidates into one origin-fair worker pool. Callers
// must continuously drain Results: publication is intentionally bounded and a
// full result stream delays slot release and graceful shutdown.
type Runner struct {
	pool            *worker.Pool
	client          CandidateClient
	executor        Executor
	supervisor      *queuev2.LeaseSupervisor
	route           queuev2.Route
	leaseTTLMS      int64
	terminalTimeout time.Duration
	now             func() time.Time

	claimGate sync.Mutex
	closed    bool
}

type candidateContextKey struct{}

// New constructs one inactive admission-before-claim coordinator.
func New(config Config, client CandidateClient, executor Executor) (*Runner, error) {
	return newRunner(config, client, executor, time.Now)
}

func newRunner(
	config Config,
	client CandidateClient,
	executor Executor,
	now func() time.Time,
) (*Runner, error) {
	if client == nil || executor == nil || now == nil ||
		config.Route.ShardID == "" ||
		config.Route.EngineOwner != "go" ||
		config.Route.RoutingEpoch < 1 ||
		config.Route.RoutingEpoch > queuev2.RedisCandidateMaxInteger ||
		config.LeaseTTLMS < 1 ||
		config.LeaseTTLMS > queuev2.RedisCandidateMaxInteger ||
		config.TerminalTimeout <= 0 {
		return nil, ErrInvalidConfig
	}
	supervisor, err := queuev2.NewLeaseSupervisor(client, queuev2.LeaseSupervisorConfig{
		HeartbeatInterval: config.HeartbeatInterval,
		HeartbeatTimeout:  config.HeartbeatTimeout,
		LeaseTTLMS:        config.LeaseTTLMS,
	})
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidConfig, err)
	}
	runner := &Runner{
		client:          client,
		executor:        executor,
		supervisor:      supervisor,
		route:           config.Route,
		leaseTTLMS:      config.LeaseTTLMS,
		terminalTimeout: config.TerminalTimeout,
		now:             now,
	}
	pool, err := worker.NewWithProcessor(config.Pool, runner)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidConfig, err)
	}
	runner.pool = pool
	return runner, nil
}

// Submit locally admits an unclaimed candidate. admissionCtx only bounds local
// admission; Candidate.Job.Context controls processing after acceptance.
func (r *Runner) Submit(admissionCtx context.Context, candidate Candidate) error {
	if r == nil || r.pool == nil || candidate.ConfigRevision < 1 ||
		candidate.ConfigRevision > queuev2.RedisCandidateMaxInteger {
		return ErrInvalidCandidate
	}
	job := candidate.Job
	if strings.TrimSpace(job.ID) == "" {
		return ErrInvalidCandidate
	}
	parent := job.Context
	if parent == nil {
		parent = context.Background()
	}
	// A private typed value preserves candidate identity even when duplicate
	// task IDs coexist in the bounded pool. No mutable ID-indexed side table is
	// involved.
	job.Context = context.WithValue(parent, candidateContextKey{}, Candidate{
		Job:            job,
		ConfigRevision: candidate.ConfigRevision,
	})
	return r.pool.Submit(admissionCtx, job)
}

// Process is the reserved worker path. It is exported only because it fulfills
// worker.Processor; callers should use Submit.
func (r *Runner) Process(ctx context.Context, job worker.Job) (result sitemap.Result, resultErr error) {
	candidate, ok := job.Context.Value(candidateContextKey{}).(Candidate)
	if !ok {
		return sitemap.Result{}, ErrInvalidCandidate
	}

	requestStartedAt, ok := r.beginClaim()
	if !ok {
		return sitemap.Result{}, ErrClosedBeforeClaim
	}
	claim := queuev2.RedisCandidateOperation{
		Kind:           "claim",
		TaskID:         candidate.Job.ID,
		Route:          r.route,
		ConfigRevision: candidate.ConfigRevision,
		LeaseTTLMS:     r.leaseTTLMS,
	}
	transition, err := r.client.Execute(ctx, claim)
	if err != nil {
		return sitemap.Result{}, fmt.Errorf("%w: client error", ErrClaimAmbiguous)
	}
	if transition.Decision != queuev2.RedisAccepted {
		if transition.Decision == queuev2.RedisTransportError ||
			!validRejectedTransition("claim", transition) {
			return sitemap.Result{}, fmt.Errorf(
				"%w: invalid or transport claim transition", ErrClaimAmbiguous,
			)
		}
		return sitemap.Result{}, &TransitionError{Operation: "claim", Err: ErrClaimNotAccepted, Transition: transition}
	}
	if err := validateAcceptedClaim(transition, r.route.RoutingEpoch, r.leaseTTLMS); err != nil {
		return sitemap.Result{}, fmt.Errorf("%w: %v", ErrClaimAmbiguous, err)
	}

	grant := queuev2.LeaseGrant{
		Fence: queuev2.LeaseFence{
			TaskID:         candidate.Job.ID,
			Route:          r.route,
			ConfigRevision: candidate.ConfigRevision,
			ClaimToken:     transition.ClaimToken,
		},
		ServerTimeMS:     transition.ServerTimeMS,
		LeaseUntilMS:     transition.Value,
		RequestStartedAt: requestStartedAt,
	}
	handle, err := r.supervisor.Start(ctx, grant)
	if err != nil {
		return sitemap.Result{}, fmt.Errorf("%w: %v", ErrClaimAmbiguous, err)
	}
	defer handle.Stop()

	executionJob := candidate.Job
	executionJob.Context = handle.Context()
	result, disposition, executeErr := r.executor.Execute(
		handle.Context(), executionJob, handle.Fence(),
	)
	// Stop is an explicit join and therefore precedes every terminal request.
	// The deferred call remains as panic cleanup.
	handle.Stop()
	if loss := handle.Loss(); loss != nil {
		return result, errors.Join(executeErr, loss)
	}
	if ctx.Err() != nil {
		return result, errors.Join(executeErr, context.Cause(ctx))
	}

	terminal, err := terminalOperation(handle.Fence(), disposition)
	if err != nil {
		return result, errors.Join(executeErr, err)
	}
	terminalCtx, cancelTerminal := context.WithTimeout(ctx, r.terminalTimeout)
	terminalTransition, terminalErr := r.client.Execute(terminalCtx, terminal)
	cancelTerminal()
	if terminalErr != nil {
		return result, errors.Join(executeErr, fmt.Errorf("%w: client error", ErrTerminalAmbiguous))
	}
	if err := validateAcceptedTerminal(terminal, terminalTransition); err != nil {
		return result, errors.Join(executeErr, err)
	}
	return result, executeErr
}

func (r *Runner) beginClaim() (time.Time, bool) {
	r.claimGate.Lock()
	defer r.claimGate.Unlock()
	if r.closed {
		return time.Time{}, false
	}
	// This timestamp is captured at the claim-start linearization point, before
	// Redis I/O. It makes the eventual local lease deadline conservative.
	return r.now(), true
}

func validateAcceptedClaim(
	transition queuev2.RedisTransition,
	routingEpoch int64,
	leaseTTLMS int64,
) error {
	if transition.Decision != queuev2.RedisAccepted ||
		transition.Reason != "claimed" ||
		transition.ServerTimeMS < 1 ||
		transition.ServerTimeMS > queuev2.RedisCandidateMaxInteger ||
		transition.ClaimToken == "" ||
		!transition.HasValue ||
		transition.Value <= transition.ServerTimeMS ||
		transition.Value > queuev2.RedisCandidateMaxInteger ||
		transition.Value-transition.ServerTimeMS != leaseTTLMS ||
		!claimTokenHasEpoch(transition.ClaimToken, routingEpoch) {
		return errors.New("invalid accepted claim transition")
	}
	return nil
}

func claimTokenHasEpoch(token string, epoch int64) bool {
	prefix, sequence, ok := strings.Cut(token, ":")
	if !ok || prefix != fmt.Sprintf("%d", epoch) || sequence == "" || sequence[0] == '0' {
		return false
	}
	for _, digit := range sequence {
		if digit < '0' || digit > '9' {
			return false
		}
	}
	// The Redis protocol bounds every canonical integer, including the token's
	// sequence. Accumulate without overflow.
	var value int64
	for _, digit := range sequence {
		if value > (queuev2.RedisCandidateMaxInteger-int64(digit-'0'))/10 {
			return false
		}
		value = value*10 + int64(digit-'0')
	}
	return value >= 1 && value <= queuev2.RedisCandidateMaxInteger
}

func terminalOperation(
	fence queuev2.LeaseFence,
	disposition Disposition,
) (queuev2.RedisCandidateOperation, error) {
	operation := queuev2.RedisCandidateOperation{
		TaskID:         fence.TaskID,
		Route:          fence.Route,
		ConfigRevision: fence.ConfigRevision,
		ClaimToken:     fence.ClaimToken,
	}
	switch disposition.Kind {
	case DispositionComplete:
		if disposition.RescheduleDelay != 0 {
			return queuev2.RedisCandidateOperation{}, ErrInvalidDisposition
		}
		operation.Kind = "complete"
	case DispositionReschedule:
		delayMS, ok := durationMillisecondsCeil(disposition.RescheduleDelay)
		if !ok {
			return queuev2.RedisCandidateOperation{}, ErrInvalidDisposition
		}
		operation.Kind = "reschedule"
		operation.RescheduleDelayMS = delayMS
	default:
		return queuev2.RedisCandidateOperation{}, ErrInvalidDisposition
	}
	return operation, nil
}

func durationMillisecondsCeil(delay time.Duration) (int64, bool) {
	if delay < 0 {
		return 0, false
	}
	if delay == 0 {
		return 0, true
	}
	value := int64(delay / time.Millisecond)
	if delay%time.Millisecond != 0 {
		if value == math.MaxInt64 {
			return 0, false
		}
		value++
	}
	return value, value <= queuev2.RedisCandidateMaxInteger
}

func validateAcceptedTerminal(
	operation queuev2.RedisCandidateOperation,
	transition queuev2.RedisTransition,
) error {
	if transition.Decision != queuev2.RedisAccepted {
		if transition.Decision == queuev2.RedisTransportError ||
			!validRejectedTransition(operation.Kind, transition) {
			return fmt.Errorf("%w: invalid or transport %s transition", ErrTerminalAmbiguous, operation.Kind)
		}
		return &TransitionError{
			Operation: operation.Kind, Err: ErrTerminalNotAccepted, Transition: transition,
		}
	}
	expectedReason := "completed"
	validValue := !transition.HasValue && transition.Value == 0
	if operation.Kind == "reschedule" {
		expectedReason = "rescheduled"
		validValue = transition.HasValue &&
			transition.Value >= transition.ServerTimeMS &&
			transition.Value-transition.ServerTimeMS == operation.RescheduleDelayMS
	}
	if transition.Reason != expectedReason ||
		transition.ServerTimeMS < 1 ||
		transition.ServerTimeMS > queuev2.RedisCandidateMaxInteger ||
		transition.ClaimToken != operation.ClaimToken ||
		!validValue ||
		transition.Value > queuev2.RedisCandidateMaxInteger {
		return fmt.Errorf("%w: invalid accepted %s transition", ErrTerminalAmbiguous, operation.Kind)
	}
	return nil
}

func validRejectedTransition(operation string, transition queuev2.RedisTransition) bool {
	if transition.ClaimToken != "" || transition.Value != 0 && !transition.HasValue {
		return false
	}
	if transition.Decision == queuev2.RedisFenced {
		return transition.ServerTimeMS >= 1 &&
			transition.ServerTimeMS <= queuev2.RedisCandidateMaxInteger &&
			!transition.HasValue &&
			validFencedReason(operation, transition.Reason)
	}
	if transition.Decision != queuev2.RedisNotCurrent ||
		!validNotCurrentReason(operation, transition.Reason) {
		return false
	}
	if transition.Reason == "redis_time_invalid" {
		return transition.ServerTimeMS == 0 && !transition.HasValue
	}
	if transition.ServerTimeMS < 1 || transition.ServerTimeMS > queuev2.RedisCandidateMaxInteger {
		return false
	}
	if operation == "claim" && transition.Reason == "not_due" {
		return transition.HasValue && transition.Value > transition.ServerTimeMS &&
			transition.Value <= queuev2.RedisCandidateMaxInteger
	}
	return !transition.HasValue
}

func validFencedReason(operation, reason string) bool {
	switch reason {
	case "shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch":
		return true
	case "config_revision_mismatch", "record_fence_mismatch":
		return operation == "claim" || operation == "complete" || operation == "reschedule"
	case "claim_token_mismatch":
		return operation == "complete" || operation == "reschedule"
	default:
		return false
	}
}

func validNotCurrentReason(operation, reason string) bool {
	common := map[string]bool{
		"redis_time_invalid":     true,
		"invalid_route":          true,
		"invalid_task_identity":  true,
		"namespace_corrupt":      true,
		"config_missing":         true,
		"record_missing":         true,
		"record_corrupt":         true,
		"conservation_violation": true,
		"state_mismatch":         true,
	}
	if common[reason] {
		return true
	}
	switch operation {
	case "claim":
		return map[string]bool{
			"not_due":                  true,
			"invalid_lease_ttl":        true,
			"numeric_overflow":         true,
			"claim_sequence_exhausted": true,
			"claim_sequence_corrupt":   true,
		}[reason]
	case "complete":
		return reason == "lease_expired"
	case "reschedule":
		return reason == "lease_expired" || reason == "invalid_reschedule_delay" || reason == "numeric_overflow"
	default:
		return false
	}
}

// TransitionError retains a rejected, already-decoded queue transition.
type TransitionError struct {
	Operation  string
	Err        error
	Transition queuev2.RedisTransition
}

func (e *TransitionError) Error() string {
	return fmt.Sprintf("queue-v2 %s: %v (%s/%s)", e.Operation, e.Err, e.Transition.Decision, e.Transition.Reason)
}

func (e *TransitionError) Unwrap() error { return e.Err }

// Results is the bounded terminal stream inherited from worker.Pool.
func (r *Runner) Results() <-chan worker.Result { return r.pool.Results() }

// Stats returns the inherited bounded-pool metrics snapshot.
func (r *Runner) Stats() worker.Stats { return r.pool.Stats() }

// Close closes the claim gate before stopping pool admission. Processor calls
// queued behind already-running workers therefore return without claiming.
func (r *Runner) Close() {
	if r == nil || r.pool == nil {
		return
	}
	r.claimGate.Lock()
	r.closed = true
	r.claimGate.Unlock()
	r.pool.Close()
}

// Shutdown starts Close and waits for all bounded results and supervisors.
func (r *Runner) Shutdown(ctx context.Context) error {
	if r == nil || r.pool == nil {
		return ErrInvalidConfig
	}
	if ctx == nil {
		return worker.ErrInvalidJob
	}
	r.Close()
	return r.pool.Shutdown(ctx)
}
