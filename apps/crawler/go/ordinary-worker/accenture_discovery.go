package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type accentureHTTPScope struct{ listing, endpoint string }

func (s accentureHTTPScope) ResourceMatches(resource string) bool {
	return resource == s.listing || resource == s.endpoint
}

// The qualified findjobs route needs only public listing cookies. Its large
// inventory stays in the native process rather than crossing the browser frame.
// Queue admission is deliberately separate from this provider implementation.
func discoverAccentureHTTPInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverAccentureHTTPInventoryWithWait(ctx, client, p, config, pauseRich)
}

func discoverAccentureHTTPInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	a, options, err := api.AccentureHTTPOptions(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || a.Endpoint != api.AccentureFindJobs || p.Provider != "accenture" || p.Profile != "accenture.http-items/v1" || p.Endpoint != config["board_url"] || config["crawler_type"] != "accenture" {
		return result, queue.ErrConfiguration
	}
	scope := accentureHTTPScope{config["board_url"], options.Endpoint}
	op := *client
	op.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	op.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) > 5 || !scope.ResourceMatches(request.URL.String()) {
			return http.ErrUseLastResponse
		}
		return nil
	}
	_, result.Response, err = fetchProviderResource(ctx, &op, scope, scope.listing, nil, nil, 32<<20)
	if err != nil {
		return result, err
	}
	found, err := api.DiscoverAccenture(ctx, a, func(call context.Context, request api.Request) (*api.Document, error) {
		if request.Method != "POST" || request.URL != scope.endpoint {
			return nil, queue.ErrConfiguration
		}
		for attempt := 0; attempt < 3; attempt++ {
			body, observed, failure := fetchProviderResource(call, &op, scope, request.URL, []byte(request.Body), request.Headers, 32<<20)
			result.Response = observed
			if call.Err() != nil {
				return nil, call.Err()
			}
			var reserved *policy.Reservation
			if errors.As(failure, &reserved) {
				return nil, failure
			}
			if failure == nil {
				if document, decodeErr := api.Decode(body); decodeErr == nil {
					return document, nil
				}
			}
			status := 0
			if observed != nil {
				status = observed.status
			}
			if status != 0 && (status < 200 || status >= 300) && status != 408 && status != 425 && status != 429 && (status < 500 || status > 599) {
				return nil, &DiscoveryError{Kind: "http_status", Status: status}
			}
			if attempt < 2 {
				delay := time.Duration(float64(time.Second) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
				if err := wait(call, delay); err != nil {
					return nil, err
				}
			}
		}
		return nil, &DiscoveryError{Kind: "json_page_failed"}
	}, nil, nil)
	if err != nil {
		return result, err
	}
	result.Truncated = found.Truncated
	for _, job := range found.Jobs {
		value, err := secondaryRichJob(map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "date_posted": job.DatePosted, "job_location_type": job.JobLocationType})
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		result.Jobs = append(result.Jobs, value)
	}
	return result, nil
}
