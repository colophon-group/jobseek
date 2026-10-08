package worker

import (
	"context"
	"io"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"time"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Configured API requests share the process-owned verified transport. Cookies
// and observations belong to this discovery, never to another board's claim.
func discoverAPISnifferInventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := queue.APISnifferMonitorOptions(config)
	if err != nil || client == nil || !queue.APISnifferMonitorResourceMatches(profile, config, o.Endpoint) {
		return result, queue.ErrConfiguration
	}
	operationClient := *client
	operationClient.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	var normal, probe, reserved *GreenhouseResponse
	fetch := func(ctx context.Context, r apisniffer.Request) (*apisniffer.Document, error) {
		if reserved != nil {
			return nil, &DiscoveryError{Kind: "publisher_reserved", Status: reserved.status}
		}
		if r.Method != o.Method || !o.ResourceMatches(r.URL) {
			return nil, queue.ErrConfiguration
		}
		attempts := o.Attempts
		if r.Probe {
			attempts = 1
		}
		var last error
		for attempt := 0; attempt < attempts; attempt++ {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			var body io.Reader
			if r.Method == http.MethodPost && r.Body != "" {
				body = strings.NewReader(r.Body)
			}
			request, err := http.NewRequestWithContext(requestCtx, r.Method, r.URL, body)
			if err != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			request.Header.Set("Accept", ordinaryAccept)
			for key, values := range r.Headers {
				request.Header[key] = append([]string{}, values...)
			}
			if body != nil && request.Header.Get("Content-Type") == "" {
				request.Header.Set("Content-Type", "application/json")
			}
			response, failure := operationClient.Do(request)
			last = &DiscoveryError{Kind: "request_failed"}
			if failure == nil {
				if response.Request == nil || response.Request.URL == nil {
					response.Body.Close()
					cancel()
					return nil, &DiscoveryError{Kind: "invalid_response"}
				}
				reservation, policy := greenhouseHeaders(response.Header)
				observed := &GreenhouseResponse{endpoint: r.URL, finalURL: response.Request.URL.String(), status: response.StatusCode, reserved: reservation == "1", policy: policy}
				if r.Probe {
					probe = observed
				} else {
					normal = observed
				}
				// A probe cannot swallow positive publisher policy. The sealed
				// observation is propagated even when the parser ignores probe errors.
				if observed.reserved {
					reserved = observed
					response.Body.Close()
					cancel()
					return nil, &DiscoveryError{Kind: "publisher_reserved", Status: observed.status}
				}
				status := response.StatusCode
				if status >= 200 && status < 300 {
					data, readErr := io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
					response.Body.Close()
					observed.bytes = len(data)
					if len(data) > 64<<20 {
						cancel()
						return nil, &DiscoveryError{Kind: "body_limit"}
					}
					if readErr == nil {
						decoded, decodeErr := apisniffer.Decode(data)
						if decodeErr == nil {
							cancel()
							return decoded, nil
						}
						last = &DiscoveryError{Kind: "invalid_inventory", Status: status}
					} else {
						last = &DiscoveryError{Kind: "body_failed"}
					}
				} else {
					response.Body.Close()
					retryable := status >= 500 || status == 408 || status == 425 || status == 429 || o.Transient403 && (status == 401 || status == 403) || len(o.EmptyResponse) > 0 && (status == 404 || status == 410)
					if !retryable {
						cancel()
						return nil, nil
					}
					last = &DiscoveryError{Kind: "http_status", Status: status}
				}
			}
			cancel()
			if attempt+1 < attempts {
				if err := pauseRich(ctx, time.Duration(float64(time.Second)*float64(int64(1)<<attempt)*(0.5+rand.Float64()))); err != nil {
					return nil, err
				}
			}
		}
		return nil, last
	}
	found, failure := apisniffer.Discover(ctx, o, fetch, pythonJoinURL)
	result.Response = normal
	if found.LastResponseProbe {
		result.Response = probe
	}
	if reserved != nil {
		result.Response = reserved
		return result, &DiscoveryError{Kind: "publisher_reserved", Status: reserved.status}
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if failure != nil {
		return result, failure
	}
	result.Truncated = found.Truncated
	for _, job := range found.Jobs {
		title, err := executor.CoerceText(job.Title)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		description, err := executor.CoerceText(job.Description)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: description, Locations: job.Locations, Language: job.Metadata["language"], DatePosted: job.DatePosted, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType, Extras: job.Extras})
	}
	return result, nil
}
