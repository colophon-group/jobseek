package worker

import (
	"context"
	"sync"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Streaming Python monitors pulse DeadlineExtender between batches. Keep the
// same inactivity budget in native monitors; an explicit operator task timeout
// and the provider-owned Njoyn maximum remain absolute deadlines.
func newClaimProcessingContext(parent context.Context, c RuntimeConfig, task queue.Task) (context.Context, context.CancelFunc, *claimLeaseGuard) {
	budget := runtimeClaimTimeout(c, task)
	grace := c.cancellationGrace - min(100*time.Millisecond, c.cancellationGrace/10)
	guard := &claimLeaseGuard{parent: parent}
	njoyn := task.Kind == queue.Monitor && task.Worker == queue.Browser && task.Config["crawler_type"] == "njoyn" && c.njoynTaskTimeout > 0
	if c.monitorProgressTimeout && task.Kind == queue.Monitor && !njoyn {
		ctx := newMonitorProgressContext(parent, budget)
		guard.progressTimeout, guard.failureGrace = ctx, grace
		return ctx, ctx.stop, guard
	}
	deadline := time.Now().Add(budget)
	ctx, cancel := context.WithDeadline(parent, deadline)
	guard.processingDeadline, guard.failureDeadline = deadline, deadline.Add(grace)
	return ctx, cancel, guard
}

// The expiration is conditional on future committed progress, so it is not a
// fixed context Deadline. Cancellation still reaches every request/SQL child.
type monitorProgressContext struct {
	parent     context.Context
	values     context.Context
	done       chan struct{}
	stopParent func() bool
	mu         sync.Mutex
	budget     time.Duration
	deadline   time.Time
	timer      *time.Timer
	err        error
	expired    bool
}

func newMonitorProgressContext(parent context.Context, budget time.Duration) *monitorProgressContext {
	// WithoutCancel preserves values without exposing a different cancelCtx:
	// child contexts and context.Cause must observe this context's own error.
	p := &monitorProgressContext{parent: parent, values: context.WithoutCancel(parent), done: make(chan struct{}), budget: budget, deadline: time.Now().Add(budget)}
	p.timer = time.AfterFunc(budget, p.expire)
	if err := parent.Err(); err != nil {
		p.finish(err)
	}
	p.stopParent = context.AfterFunc(parent, func() { p.finish(parent.Err()) })
	return p
}

func (p *monitorProgressContext) Deadline() (time.Time, bool) { return p.parent.Deadline() }
func (p *monitorProgressContext) Done() <-chan struct{}       { return p.done }
func (p *monitorProgressContext) Value(key any) any           { return p.values.Value(key) }

func (p *monitorProgressContext) Err() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.err
}

func (p *monitorProgressContext) expire() {
	p.mu.Lock()
	if p.err != nil || time.Now().Before(p.deadline) {
		p.mu.Unlock()
		return
	}
	p.err = p.parent.Err()
	if p.err == nil {
		p.expired, p.err = true, context.DeadlineExceeded
	}
	close(p.done)
	p.mu.Unlock()
}

func (p *monitorProgressContext) pulse() bool {
	p.mu.Lock()
	if p.err != nil {
		p.mu.Unlock()
		return false
	}
	if !time.Now().Before(p.deadline) {
		p.mu.Unlock()
		p.expire()
		return false
	}
	if err := p.parent.Err(); err != nil {
		p.mu.Unlock()
		p.finish(err)
		return false
	}
	p.deadline = time.Now().Add(p.budget)
	p.timer.Reset(p.budget)
	p.mu.Unlock()
	return true
}

func (p *monitorProgressContext) expiredDeadline() (time.Time, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.deadline, p.expired
}

func (p *monitorProgressContext) finish(err error) {
	p.mu.Lock()
	if p.err == nil {
		p.err = err
		p.timer.Stop()
		close(p.done)
	}
	p.mu.Unlock()
}

func (p *monitorProgressContext) stop() {
	p.finish(context.Canceled)
	p.stopParent()
}

// Only successful authoritative batch writes/publication call this helper.
// Heartbeats or repeated upstream responses cannot keep a stalled task alive.
func pulseMonitorProgress(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	guard, _ := ctx.Value(claimLeaseGuardKey{}).(*claimLeaseGuard)
	if guard == nil {
		return nil
	}
	guard.mu.Lock()
	settled := guard.settled
	guard.mu.Unlock()
	if settled {
		return nil
	}
	if guard.progressTimeout != nil && !guard.progressTimeout.pulse() {
		if err := ctx.Err(); err != nil {
			return err
		}
		return context.Canceled
	}
	if guard.onProgress != nil {
		guard.onProgress()
	}
	return nil
}
