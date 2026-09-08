package queuev2

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sync"
	"time"
)

var (
	ErrInvalidLease               = errors.New("invalid queue-v2 lease")
	ErrInvalidLeaseSupervisor     = errors.New("invalid queue-v2 lease supervisor")
	ErrInvalidHeartbeatTransition = errors.New("invalid queue-v2 heartbeat transition")
	ErrLeaseDeadlineElapsed       = errors.New("queue-v2 lease deadline elapsed")
)

// LeaseFence is the complete identity a future authoritative writer must
// validate inside the same transaction as its mutation. Cancellation is only
// defense in depth and never authorizes a write.
type LeaseFence struct {
	TaskID         string
	Route          Route
	ConfigRevision int64
	ClaimToken     string
}

func (f LeaseFence) heartbeat(leaseTTLMS, previousLeaseUntil int64) RedisCandidateOperation {
	return RedisCandidateOperation{
		Kind:               "heartbeat",
		TaskID:             f.TaskID,
		Route:              f.Route,
		ConfigRevision:     f.ConfigRevision,
		ClaimToken:         f.ClaimToken,
		LeaseTTLMS:         leaseTTLMS,
		PreviousLeaseUntil: previousLeaseUntil,
	}
}

// LeaseGrant binds a full fence to the Redis time and lease deadline returned
// by claim. RequestStartedAt must be captured immediately before sending the
// claim request. It establishes a conservative local monotonic deadline
// without comparing the Redis and worker wall clocks.
type LeaseGrant struct {
	Fence            LeaseFence
	ServerTimeMS     int64
	LeaseUntilMS     int64
	RequestStartedAt time.Time
}

// LeaseLostError is the retained first fail-closed heartbeat outcome for a
// lease. Its transition is exposed only as a value copy.
type LeaseLostError struct {
	transition RedisTransition
	cause      error
}

func newLeaseLostError(transition RedisTransition, cause error) *LeaseLostError {
	return &LeaseLostError{transition: transition, cause: cause}
}

func (e *LeaseLostError) Error() string {
	if e == nil {
		return "queue-v2 lease lost"
	}
	return fmt.Sprintf(
		"queue-v2 lease lost: %s/%s",
		e.transition.Decision,
		e.transition.Reason,
	)
}

func (e *LeaseLostError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.cause
}

func (e *LeaseLostError) Transition() RedisTransition {
	if e == nil {
		return RedisTransition{}
	}
	return e.transition
}

// HeartbeatExecutor is the queue-v2 client surface needed by the supervisor.
// RedisCandidateClient satisfies it. Implementations must observe ctx.
type HeartbeatExecutor interface {
	Execute(context.Context, RedisCandidateOperation) (RedisTransition, error)
}

var _ HeartbeatExecutor = (*RedisCandidateClient)(nil)

// LeaseSupervisorConfig fixes heartbeat timing. Active supervisors must be
// bounded by the caller's worker capacity; this contract does not create a
// dequeue or scheduling loop.
type LeaseSupervisorConfig struct {
	HeartbeatInterval time.Duration
	HeartbeatTimeout  time.Duration
	LeaseTTLMS        int64
}

type leaseTicker interface {
	C() <-chan time.Time
	Stop()
}

type realLeaseTicker struct{ ticker *time.Ticker }

func (t realLeaseTicker) C() <-chan time.Time { return t.ticker.C }
func (t realLeaseTicker) Stop()               { t.ticker.Stop() }

type leaseTimer interface {
	C() <-chan time.Time
	Stop() bool
	Reset(time.Duration) bool
}

type realLeaseTimer struct{ timer *time.Timer }

func (t realLeaseTimer) C() <-chan time.Time        { return t.timer.C }
func (t realLeaseTimer) Stop() bool                 { return t.timer.Stop() }
func (t realLeaseTimer) Reset(d time.Duration) bool { return t.timer.Reset(d) }

// leaseParentView retains parent values and deadline metadata while all
// cancellation is serialized explicitly through LeaseHandle.
type leaseParentView struct{ context.Context }

func (leaseParentView) Done() <-chan struct{} { return nil }
func (leaseParentView) Err() error            { return nil }

// LeaseSupervisor converts every non-accepted or ambiguous heartbeat outcome
// into cancellation of a derived work context.
type LeaseSupervisor struct {
	executor  HeartbeatExecutor
	config    LeaseSupervisorConfig
	ttl       time.Duration
	newTicker func(time.Duration) leaseTicker
	newTimer  func(time.Duration) leaseTimer
}

func NewLeaseSupervisor(
	executor HeartbeatExecutor,
	config LeaseSupervisorConfig,
) (*LeaseSupervisor, error) {
	if executor == nil ||
		config.HeartbeatInterval <= 0 ||
		config.HeartbeatTimeout <= 0 ||
		boundedInt(config.LeaseTTLMS, 1, "lease TTL") != nil ||
		config.LeaseTTLMS > math.MaxInt64/int64(time.Millisecond) {
		return nil, ErrInvalidLeaseSupervisor
	}
	ttl := time.Duration(config.LeaseTTLMS) * time.Millisecond
	if config.HeartbeatTimeout >= ttl ||
		config.HeartbeatInterval >= ttl-config.HeartbeatTimeout {
		return nil, ErrInvalidLeaseSupervisor
	}
	return &LeaseSupervisor{
		executor: executor,
		config:   config,
		ttl:      ttl,
		newTicker: func(interval time.Duration) leaseTicker {
			return realLeaseTicker{ticker: time.NewTicker(interval)}
		},
		newTimer: func(duration time.Duration) leaseTimer {
			return realLeaseTimer{timer: time.NewTimer(duration)}
		},
	}, nil
}

// LeaseHandle owns one derived work context and one bounded heartbeat
// goroutine. Stop must be called after work finishes and before complete or
// reschedule so a final heartbeat cannot race the terminal queue transition.
type LeaseHandle struct {
	fence  LeaseFence
	parent context.Context

	workContext context.Context
	cancelWork  context.CancelCauseFunc
	stopBeat    context.CancelFunc
	stopParent  func() bool
	parentDone  chan struct{}
	done        chan struct{}
	stopOnce    sync.Once

	mu           sync.RWMutex
	leaseUntilMS int64
	deadline     time.Time
	loss         *LeaseLostError
	lost         chan struct{}
	stopped      bool
}

// Start validates the full claim grant and starts one heartbeat goroutine.
// Parent cancellation is serialized with lease loss and never manufactures a
// lease-loss outcome.
func (s *LeaseSupervisor) Start(
	parent context.Context,
	grant LeaseGrant,
) (*LeaseHandle, error) {
	if s == nil || s.executor == nil || parent == nil {
		return nil, ErrInvalidLeaseSupervisor
	}
	if parent.Err() != nil {
		return nil, context.Cause(parent)
	}
	now := time.Now()
	if grant.RequestStartedAt.IsZero() || grant.RequestStartedAt.After(now) ||
		boundedInt(grant.ServerTimeMS, 1, "server time") != nil ||
		boundedInt(grant.LeaseUntilMS, 1, "lease deadline") != nil ||
		grant.LeaseUntilMS <= grant.ServerTimeMS ||
		grant.LeaseUntilMS-grant.ServerTimeMS != s.config.LeaseTTLMS {
		return nil, ErrInvalidLease
	}
	if err := validateRedisOperation(
		grant.Fence.heartbeat(s.config.LeaseTTLMS, grant.LeaseUntilMS),
	); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidLease, err)
	}
	deadline := grant.RequestStartedAt.Add(s.ttl)
	if !deadline.After(now) {
		return nil, ErrInvalidLease
	}

	// Keep parent values, but serialize all cancellation causes through the
	// handle mutex so Lost, Loss, and context.Cause cannot disagree.
	workContext, cancelWork := context.WithCancelCause(leaseParentView{Context: parent})
	heartbeatContext, stopBeat := context.WithCancel(parent)
	handle := &LeaseHandle{
		fence:        grant.Fence,
		parent:       parent,
		workContext:  workContext,
		cancelWork:   cancelWork,
		stopBeat:     stopBeat,
		parentDone:   make(chan struct{}),
		done:         make(chan struct{}),
		leaseUntilMS: grant.LeaseUntilMS,
		deadline:     deadline,
		lost:         make(chan struct{}),
	}
	handle.stopParent = context.AfterFunc(parent, func() {
		handle.cancelFromParent()
		close(handle.parentDone)
	})
	if parent.Err() != nil {
		handle.cancelFromParent()
	}
	go s.run(heartbeatContext, handle)
	return handle, nil
}

func (s *LeaseSupervisor) run(ctx context.Context, handle *LeaseHandle) {
	defer close(handle.done)
	ticker := s.newTicker(s.config.HeartbeatInterval)
	defer ticker.Stop()
	deadlineTimer := s.newTimer(time.Until(handle.deadlineSnapshot()))
	defer stopTimer(deadlineTimer)

	for {
		select {
		case <-ctx.Done():
			handle.cancelFromParent()
			return
		case <-deadlineTimer.C():
			handle.markLost(newLeaseLostError(RedisTransition{
				Decision: RedisTransportError,
				Reason:   "lease_deadline_elapsed",
			}, ErrLeaseDeadlineElapsed))
			return
		case <-ticker.C():
			previousLeaseUntil, currentDeadline := handle.leaseSnapshot()
			if !time.Now().Before(currentDeadline) {
				handle.markLost(newLeaseLostError(RedisTransition{
					Decision: RedisTransportError,
					Reason:   "lease_deadline_elapsed",
				}, ErrLeaseDeadlineElapsed))
				return
			}

			heartbeatStartedAt := time.Now()
			callDeadline := heartbeatStartedAt.Add(s.config.HeartbeatTimeout)
			if currentDeadline.Before(callDeadline) {
				callDeadline = currentDeadline
			}
			heartbeatContext, cancel := context.WithDeadline(ctx, callDeadline)
			transition, err := s.executor.Execute(
				heartbeatContext,
				handle.fence.heartbeat(s.config.LeaseTTLMS, previousLeaseUntil),
			)
			cancel()
			if ctx.Err() != nil {
				handle.cancelFromParent()
				return
			}
			if !time.Now().Before(currentDeadline) {
				handle.markLost(newLeaseLostError(RedisTransition{
					Decision: RedisTransportError,
					Reason:   "lease_deadline_elapsed",
				}, ErrLeaseDeadlineElapsed))
				return
			}

			if err != nil {
				handle.markLost(newLeaseLostError(RedisTransition{
					Decision: RedisTransportError,
					Reason:   "heartbeat_client_error",
				}, err))
				return
			}
			if transition.Decision != RedisAccepted {
				if err := validLeaseLossTransition(transition); err != nil {
					transition = RedisTransition{
						Decision: RedisTransportError,
						Reason:   "invalid_heartbeat_transition",
					}
					handle.markLost(newLeaseLostError(transition, err))
				} else {
					handle.markLost(newLeaseLostError(transition, nil))
				}
				return
			}
			if err := validateAcceptedHeartbeat(
				transition,
				handle.fence,
				previousLeaseUntil,
				s.config.LeaseTTLMS,
			); err != nil {
				transition = RedisTransition{
					Decision: RedisTransportError,
					Reason:   "invalid_heartbeat_transition",
				}
				handle.markLost(newLeaseLostError(transition, err))
				return
			}
			newDeadline := heartbeatStartedAt.Add(s.ttl)
			if !handle.extend(transition.Value, newDeadline) {
				return
			}
			resetTimer(deadlineTimer, time.Until(newDeadline))
		}
	}
}

func stopTimer(timer leaseTimer) {
	if !timer.Stop() {
		select {
		case <-timer.C():
		default:
		}
	}
}

func resetTimer(timer leaseTimer, duration time.Duration) {
	stopTimer(timer)
	timer.Reset(duration)
}

func validLeaseLossTransition(transition RedisTransition) error {
	if transition.Decision != RedisFenced &&
		transition.Decision != RedisNotCurrent &&
		transition.Decision != RedisTransportError {
		return ErrInvalidHeartbeatTransition
	}
	if transition.Reason == "" || transition.HasValue || transition.Value != 0 ||
		transition.ClaimToken != "" {
		return ErrInvalidHeartbeatTransition
	}
	switch transition.Decision {
	case RedisFenced:
		if !legalFenceReason("heartbeat", transition.Reason) ||
			boundedInt(transition.ServerTimeMS, 1, "server time") != nil {
			return ErrInvalidHeartbeatTransition
		}
	case RedisNotCurrent:
		if !notCurrentReasons["heartbeat"][transition.Reason] {
			return ErrInvalidHeartbeatTransition
		}
		if transition.Reason == "redis_time_invalid" {
			if transition.ServerTimeMS != 0 {
				return ErrInvalidHeartbeatTransition
			}
		} else if boundedInt(transition.ServerTimeMS, 1, "server time") != nil {
			return ErrInvalidHeartbeatTransition
		}
	case RedisTransportError:
		if (transition.Reason != "redis_error" && transition.Reason != "invalid_redis_reply") ||
			transition.ServerTimeMS != 0 {
			return ErrInvalidHeartbeatTransition
		}
	}
	return nil
}

func validateAcceptedHeartbeat(
	transition RedisTransition,
	fence LeaseFence,
	previousLeaseUntil int64,
	leaseTTLMS int64,
) error {
	if transition.Decision != RedisAccepted ||
		transition.Reason != "lease_extended" ||
		transition.ClaimToken != fence.ClaimToken ||
		!transition.HasValue ||
		transition.ServerTimeMS < 1 ||
		transition.ServerTimeMS >= previousLeaseUntil ||
		transition.Value <= transition.ServerTimeMS ||
		transition.Value-transition.ServerTimeMS != leaseTTLMS ||
		transition.Value <= previousLeaseUntil ||
		boundedInt(transition.ServerTimeMS, 1, "server time") != nil ||
		boundedInt(transition.Value, 1, "lease deadline") != nil {
		return ErrInvalidHeartbeatTransition
	}
	return nil
}

func (h *LeaseHandle) cancelFromParent() {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped || h.loss != nil || h.parent.Err() == nil {
		return
	}
	h.stopped = true
	h.cancelWork(context.Cause(h.parent))
}

func (h *LeaseHandle) markLost(loss *LeaseLostError) {
	if loss == nil {
		loss = newLeaseLostError(RedisTransition{
			Decision: RedisTransportError,
			Reason:   "invalid_heartbeat_transition",
		}, ErrInvalidHeartbeatTransition)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped || h.loss != nil {
		return
	}
	if h.parent.Err() != nil {
		h.stopped = true
		h.cancelWork(context.Cause(h.parent))
		return
	}
	h.loss = loss
	h.cancelWork(loss)
	close(h.lost)
}

func (h *LeaseHandle) extend(leaseUntilMS int64, deadline time.Time) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped || h.loss != nil {
		return false
	}
	if h.parent.Err() != nil {
		h.stopped = true
		h.cancelWork(context.Cause(h.parent))
		return false
	}
	if leaseUntilMS <= h.leaseUntilMS || !deadline.After(h.deadline) {
		return false
	}
	h.leaseUntilMS = leaseUntilMS
	h.deadline = deadline
	return true
}

func (h *LeaseHandle) leaseSnapshot() (int64, time.Time) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.leaseUntilMS, h.deadline
}

func (h *LeaseHandle) deadlineSnapshot() time.Time {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.deadline
}

func (h *LeaseHandle) Context() context.Context { return h.workContext }
func (h *LeaseHandle) Fence() LeaseFence        { return h.fence }
func (h *LeaseHandle) Lost() <-chan struct{}    { return h.lost }

func (h *LeaseHandle) LeaseUntilMS() int64 {
	leaseUntilMS, _ := h.leaseSnapshot()
	return leaseUntilMS
}

// Loss returns the retained first lease-loss cause, or nil while current.
func (h *LeaseHandle) Loss() *LeaseLostError {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.loss
}

// Stop is idempotent. Its mutex acquisition is the finish/loss linearization
// point: a loss recorded first remains authoritative; a Stop recorded first
// suppresses later outcomes. Stop joins the heartbeat goroutine and releases
// the derived work context without recording lease loss.
func (h *LeaseHandle) Stop() {
	if h == nil {
		return
	}
	h.stopOnce.Do(func() {
		h.mu.Lock()
		h.stopped = true
		h.stopBeat()
		if h.loss == nil {
			if h.parent.Err() != nil {
				h.cancelWork(context.Cause(h.parent))
			} else {
				h.cancelWork(context.Canceled)
			}
		}
		h.mu.Unlock()
		if h.stopParent != nil && h.stopParent() {
			close(h.parentDone)
		}
		if h.parentDone != nil {
			<-h.parentDone
		}
	})
	<-h.done
}
