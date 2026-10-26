package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type tupu360Scope struct {
	profile queue.GreenhouseMonitorProfile
	config  map[string]string
}

func (s tupu360Scope) PublisherPolicyOnStatus() bool { return true }

func (s tupu360Scope) ResourceMatches(source string) bool {
	return queue.DOMMonitorResourceMatches(s.profile, s.config, source)
}

func discoverTupu360Inventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string, pause func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if client == nil || !queue.Tupu360Monitor(config) || profile.Provider != "dom" || profile.Endpoint != config["board_url"] {
		return result, queue.ErrConfiguration
	}
	scope := tupu360Scope{profile, config}
	expected := -1
	seen := map[string]bool{}
	for page := 0; page < 5000; page++ {
		endpoint, _ := url.Parse(profile.Endpoint)
		values := endpoint.Query()
		values.Set("offset", strconv.Itoa(len(result.Jobs)))
		values.Set("max", "100")
		endpoint.RawQuery = values.Encode()
		var body []byte
		var fetchErr error
		for attempt := 0; attempt < 3; attempt++ {
			body, result.Response, fetchErr = fetchProviderResource(ctx, client, scope, endpoint.String(), nil, nil, 2<<20)
			var reserved *policy.Reservation
			if ctx.Err() != nil {
				result.Jobs = nil
				return result, ctx.Err()
			}
			if errors.As(fetchErr, &reserved) {
				result.Jobs = nil
				return result, fetchErr
			}
			status := 0
			if result.Response != nil {
				status = result.Response.status
			}
			if fetchErr == nil && status == 200 && len(body) > 0 {
				break
			}
			if status != 0 && status != 200 && status != 408 && status != 425 && status != 429 && (status < 500 || status > 599) {
				result.Jobs = nil
				return result, &DiscoveryError{Kind: "inventory_failed", Status: status}
			}
			if attempt == 2 {
				result.Jobs = nil
				return result, &DiscoveryError{Kind: "inventory_failed", cause: fetchErr}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if err := pause(ctx, delay); err != nil {
				result.Jobs = nil
				return result, err
			}
		}
		total, jobs, err := api.ParseTupu360Listing(jsonld.DecodeDocument(body, result.Response.contentType), profile.Endpoint)
		if err != nil || expected >= 0 && total != expected || len(jobs) == 0 && total > 0 {
			result.Jobs = nil
			return result, &DiscoveryError{Kind: "inventory_failed", cause: err}
		}
		expected = total
		for _, fields := range jobs {
			id := fields["metadata"].(map[string]any)["position_id"].(string)
			if seen[id] {
				result.Jobs = nil
				return result, &DiscoveryError{Kind: "inventory_failed"}
			}
			seen[id] = true
			job, err := secondaryRichJob(fields)
			if err != nil {
				result.Jobs = nil
				return result, err
			}
			result.Jobs = append(result.Jobs, job)
		}
		if len(result.Jobs) == total {
			return result, nil
		}
		if len(result.Jobs) > total {
			result.Jobs = nil
			return result, &DiscoveryError{Kind: "inventory_failed"}
		}
	}
	result.Jobs = nil
	return result, &DiscoveryError{Kind: "inventory_failed"}
}
