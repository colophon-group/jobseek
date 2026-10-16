package worker

import (
	"context"
	"sync"
	"sync/atomic"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Reserve browser execution capacity before taking its opaque queue lease.
// Renderer waiters must not occupy every discovery slot and starve HTTP work.
// This limits browser claims across monitors and details together.
type browserClaimCapacity struct {
	slots chan struct{}
	turn  atomic.Uint64
	held  sync.Map // *queue.Claim -> reservation; at most cap(slots) entries
}

func (b *browserClaimCapacity) claim(ctx context.Context, pop func(context.Context, queue.WorkerType) (*queue.Claim, error)) (*queue.Claim, error) {
	if b == nil {
		return pop(ctx, queue.Simple)
	}
	first, second := queue.Simple, queue.Browser
	if b.turn.Add(1)%2 == 0 {
		first, second = second, first
	}
	for _, lane := range []queue.WorkerType{first, second} {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if lane == queue.Browser {
			select {
			case b.slots <- struct{}{}:
			default:
				continue
			}
		}
		claim, err := pop(ctx, lane)
		if lane == queue.Browser {
			if err != nil || claim == nil {
				<-b.slots
			} else {
				b.held.Store(claim, struct{}{})
			}
		}
		if err != nil || claim != nil {
			return claim, err
		}
	}
	return nil, nil
}

// Called only after the leased runner and its heartbeat have drained. A drain
// timeout retains capacity until process exit because execution may still run.
func (b *browserClaimCapacity) release(claim *queue.Claim) {
	if b != nil {
		if _, held := b.held.LoadAndDelete(claim); held {
			<-b.slots
		}
	}
}
