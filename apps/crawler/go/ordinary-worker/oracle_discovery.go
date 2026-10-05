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
	rules, err := queue.OracleMonitorURLRules(config)
	if err != nil {
		return result, err
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
		body, response, err := fetchOraclePage(ctx, &operation, endpoint)
		result.Response = response
		return body, err
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
		source, err := rules.Apply(job.URL)
		if err != nil {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "provider_boundary"}
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: source, Title: title, Locations: job.Locations, DatePosted: job.DatePosted, EmploymentType: job.EmploymentType})
	}
	return result, nil
}

// Oracle monitor and detail use the same bounded public-API retry policy.
func fetchOraclePage(ctx context.Context, client *http.Client, endpoint string) ([]byte, *GreenhouseResponse, error) {
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		body, response, failure := richPage(requestCtx, client, endpoint, false)
		cancel()

		if ctx.Err() != nil {
			return nil, response, ctx.Err()
		}
		if response == nil || response.reserved || failure == nil {
			return body, response, failure
		}
		status := response.status
		transient := status == 302 || status == 403 || status == 429 || status == 500 || status == 502 || status == 503 || status == 504
		if !transient || attempt == 2 {
			return nil, response, failure
		}
		if err := pauseRich(ctx, time.Duration(float64(3*time.Second)*float64(int64(1)<<attempt)*(0.8+0.4*rand.Float64()))); err != nil {
			return nil, response, err
		}
	}
	return nil, nil, oracle.ErrInventory
}
