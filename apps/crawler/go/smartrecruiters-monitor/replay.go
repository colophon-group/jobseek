package smartrecruiters

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// Replay consumes captured response objects without constructing an HTTP client.
// Repeated endpoint responses preserve snapshot-change retry behavior.
func Replay(ctx context.Context, boardURL string, metadata Object, responses map[string][]Object) (Inventory, error) {
	opt, err := OptionsFromMetadata(boardURL, metadata)
	if err != nil {
		return Inventory{}, err
	}
	positions := map[string]int{}
	var mu sync.Mutex
	get := func(ctx context.Context, endpoint string, _ int) (Object, error) {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		mu.Lock()
		defer mu.Unlock()
		values := responses[endpoint]
		if len(values) == 0 {
			return nil, fmt.Errorf("missing captured endpoint: %s", endpoint)
		}
		index := positions[endpoint]
		positions[endpoint]++
		if index >= len(values) {
			index = len(values) - 1
		}
		return values[index], nil
	}
	return Discover(ctx, opt, get, func(context.Context, time.Duration) error { return nil })
}
