package main

import (
	"context"
	"errors"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var errSessionWarmup = errors.New("browser session warmup failed")

// Match the original open_page warmup: DOM-content-loaded, thirty seconds,
// followed by the real navigation in the same context. No warmup content or
// captured credentials become inventory or cross the service response.
func executeSessionWarmup(ctx, target context.Context, endpoint string) error {
	result, err := executeOnTarget(ctx, target, Task{URL: endpoint, Navigation: &navigationOptions{
		wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: 30 * time.Second,
	}})
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errSessionWarmup
	}
	if err := policy.Check(result.ResourcePolicy, result.HTML, result.FinalURL); err != nil {
		return err
	}
	if result.Status < 200 || result.Status >= 300 {
		return errSessionWarmup
	}
	return nil
}
