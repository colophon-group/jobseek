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
