package worker

import (
	"context"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Finder pages use the existing verified HTTP transport and shared canonical
// writer. A later failed page cannot publish a partial complete inventory.
func discoverOracleInventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := queue.OracleMonitorOptions(config)
	if err != nil || client == nil || !queue.OracleMonitorResourceMatches(profile, config, o.Endpoint()) {
		return result, queue.ErrConfiguration
	}
	operation := *client
	operation.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	operation.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(ctx context.Context, endpoint string) ([]byte, error) {
		if !o.ResourceMatches(endpoint) {
			return nil, queue.ErrConfiguration
		}
		for attempt := 0; attempt < 3; attempt++ {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			body, response, failure := richPage(requestCtx, &operation, endpoint, false)
			cancel()
			result.Response = response
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if response == nil || response.reserved || failure == nil {
				return body, failure
			}
			status := response.status
			transient := status == 302 || status == 403 || status == 429 || status == 500 || status == 502 || status == 503 || status == 504
			if !transient || attempt == 2 {
				return nil, failure
			}
			if err := pauseRich(ctx, time.Duration(float64(3*time.Second)*float64(int64(1)<<attempt)*(0.8+0.4*rand.Float64()))); err != nil {
				return nil, err
			}
		}
		return nil, oracle.ErrInventory
	}
	found, err := oracle.Discover(ctx, o, fetch)
	if err != nil {
		return result, err
	}
	result.Truncated = found.Truncated
	for _, job := range found.Jobs {
		title, err := executor.CoerceText(job.Title)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Locations: job.Locations, DatePosted: job.DatePosted, EmploymentType: job.EmploymentType})
	}
	return result, nil
}
