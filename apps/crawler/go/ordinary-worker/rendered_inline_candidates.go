package worker

import (
	"context"
	"errors"
	"strings"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Navigate only the inspected ordered read candidates. Inventory URLs and the
// claim/config binding keep the public board as their canonical source.
func collectRenderedInlineCandidates(ctx context.Context, p queue.GreenhouseMonitorProfile, config map[string]string, o api.InlineMonitorOptions, navigate func(queue.GreenhouseMonitorProfile, int) (*runtimev1.BrowserResult, error)) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if len(o.Candidates) < 2 || navigate == nil || p.Endpoint != o.BoardURL {
		return result, queue.ErrConfiguration
	}
	var failure error
	for _, candidate := range o.Candidates {
		request := p
		request.Endpoint = candidate.URL
		for attempt := 0; attempt < 2; attempt++ {
			if e := ctx.Err(); e != nil {
				return result, e
			}
			value, e := navigate(request, attempt)
			if e != nil {
				return result, e
			}
			result, failure = parseHeldRenderedMonitor(ctx, request, config, value)
			if result.Response != nil && result.Response.reserved {
				return result, failure
			}
			if failure == nil {
				return result, nil
			}
			if errors.Is(failure, executor.ErrBotChallenge) {
				if attempt == 0 {
					continue
				}
				break
			}
			var status *executor.NavigationHTTPError
			if errors.As(failure, &status) {
				break
			}
			if o.Contains != "" {
				source, e := executor.RenderedHTML(value, candidate.URL)
				if e == nil && !strings.Contains(source, o.Contains) {
					break
				}
			}
			// Missing/corrupt evidence and parsing failures never become fallback or
			// absence authority. Existing source-bound browser failure remains closed.
			return result, failure
		}
	}
	return result, failure
}
