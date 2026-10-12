package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Waiting monitors retain the outer leased runner's heartbeat and cancellation.
// Detail claims use discovery capacity without consuming a monitor slot.
func executeWithMonitorSlot(ctx context.Context, slots chan struct{}, kind queue.Kind, execute func() (*GreenhouseClaimResult, error)) (*GreenhouseClaimResult, error) {
	if kind == queue.Monitor && slots != nil {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case slots <- struct{}{}:
		}
		defer func() { <-slots }()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return execute()
}

// Reserve monitor capacity before popping a lease. Excess discovery slots
// claim details directly instead of retaining monitors that cannot execute.
func claimWithMonitorCapacity(ctx context.Context, s runtimeServices) (*queue.Claim, bool, error) {
	reserved := false
	if s.monitorSlots != nil && s.claimDetail != nil {
		select {
		case s.monitorSlots <- struct{}{}:
			reserved = true
		default:
			claim, err := s.claimDetail(ctx)
			if err == nil && claim != nil && claim.Descriptor().Kind != queue.Scrape {
				return nil, false, queue.ErrConfiguration
			}
			return claim, false, err
		}
	}
	claim, err := s.claim(ctx)
	if reserved && (err != nil || claim == nil || claim.Descriptor().Kind != queue.Monitor) {
		<-s.monitorSlots
		reserved = false
	}
	return claim, reserved, err
}
