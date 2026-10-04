package worker

import (
	"context"
	"encoding/json"
	"io"
	"math/rand/v2"
	"net/http"
	"strconv"
	"strings"
	"time"

	ashby "github.com/colophon-group/jobseek/apps/crawler/go/ashby-monitor"
	lever "github.com/colophon-group/jobseek/apps/crawler/go/lever-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	pinpoint "github.com/colophon-group/jobseek/apps/crawler/go/pinpoint-monitor"
	recruitee "github.com/colophon-group/jobseek/apps/crawler/go/recruitee-monitor"
)

type RichDiscovery struct {
	Jobs      []RichMonitorJob
	Truncated bool
	Response  *GreenhouseResponse
}

func pauseRich(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Every page uses the process-owned verified client. Completed response evidence
// is kept separately from inventory; a later page failure supplies no inventory.
func richPage(ctx context.Context, client *http.Client, endpoint string, leverPage bool) ([]byte, *GreenhouseResponse, error) {
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil || client == nil {
		return nil, nil, &DiscoveryError{Kind: "invalid_configuration"}
	}
	request.Header.Set("User-Agent", ordinaryUserAgent)
	request.Header.Set("Accept", ordinaryAccept)
	if leverPage {
		request.Header.Set("Accept", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, nil, &DiscoveryError{Kind: "request_failed", cause: ctx.Err()}
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<20+1))
	if err != nil {
		return nil, nil, &DiscoveryError{Kind: "body_failed", cause: ctx.Err()}
	}
	if len(body) > 64<<20 {
		return nil, nil, &DiscoveryError{Kind: "body_limit"}
	}
	if ctx.Err() != nil {
		return nil, nil, &DiscoveryError{Kind: "body_failed", cause: ctx.Err()}
	}
	if response.Request == nil || response.Request.URL == nil {
		return nil, nil, &DiscoveryError{Kind: "invalid_response"}
	}
	reservation, policy := greenhouseHeaders(response.Header)
	observed := &GreenhouseResponse{endpoint: endpoint, finalURL: response.Request.URL.String(), status: response.StatusCode, bytes: len(body), reserved: reservation == "1" && (!leverPage || response.StatusCode == 200), policy: policy}
	if observed.reserved {
		return nil, observed, &DiscoveryError{Kind: "publisher_reserved", Status: observed.status}
	}
	if leverPage && response.StatusCode != 200 || !leverPage && (response.StatusCode < 200 || response.StatusCode >= 300) {
		kind := "http_status"
		if response.StatusCode == 404 {
			kind = "provider_gone"
		}
		return nil, observed, &DiscoveryError{Kind: kind, Status: observed.status}
	}
	body, err = greenhouseJSONBytes(body)
	if err != nil {
		return nil, observed, &DiscoveryError{Kind: "invalid_inventory", Status: observed.status}
	}
	return body, observed, nil
}

func DiscoverRichMonitor(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if profile.Profile == "rss.teamtailor-skip/v1" {
		return discoverTeamtailorRich(ctx, client, profile)
	}
	if profile.Profile == "rss.successfactors-skip/v1" {
		return discoverSuccessFactorsRich(ctx, client, profile)
	}
	if profile.Provider == "greenhouse" {
		found, err := DiscoverGreenhouse(ctx, client, profile.Token)
		result.Response = found.Response
		result.Truncated = found.Inventory.Truncated
		if err != nil {
			return result, err
		}
		for _, job := range found.Inventory.Jobs {
			result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: job.Title, Description: job.Description, Locations: job.Locations, Language: job.Language, DatePosted: job.DatePosted, Metadata: job.Metadata})
		}
		return result, nil
	}
	if profile.Provider == "ashby" {
		body, response, err := richPage(ctx, client, profile.Endpoint, false)
		result.Response = response
		if err != nil {
			return result, err
		}
		inventory, err := ashby.Parse(body)
		if err != nil {
			return result, &DiscoveryError{Kind: "invalid_inventory"}
		}
		result.Truncated = inventory.Truncated
		for _, job := range inventory.Jobs {
			title, err := executor.CoerceText(job.Title)
			if err != nil {
				return RichDiscovery{Response: response}, &DiscoveryError{Kind: "invalid_inventory"}
			}
			description, err := executor.CoerceText(job.Description)
			if err != nil {
				return RichDiscovery{Response: response}, &DiscoveryError{Kind: "invalid_inventory"}
			}
			var locationType any
			if job.JobLocationType != nil {
				locationType = *job.JobLocationType
			}
			result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: description, Locations: job.Locations, DatePosted: job.DatePosted, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: locationType})
		}
		return result, nil
	}
	if profile.Provider == "recruitee" || profile.Provider == "pinpoint" {
		body, response, err := richPage(ctx, client, profile.Endpoint, false)
		result.Response = response
		if err != nil {
			// Pinpoint raises an ordinary status error for 404; Recruitee
			// explicitly supplies the provider-gone confirmation contract.
			if profile.Provider == "pinpoint" && response != nil && response.status == 404 && !response.reserved {
				err = &DiscoveryError{Kind: "http_status", Status: 404}
			}
			return result, err
		}
		var jobs []recruitee.Job
		if profile.Provider == "recruitee" {
			inventory, failure := recruitee.Parse(body)
			jobs, result.Truncated, err = inventory.Jobs, inventory.Truncated, failure
		} else {
			inventory, failure := pinpoint.Parse(body)
			result.Truncated, err = inventory.Truncated, failure
			for _, job := range inventory.Jobs {
				// Both parsers expose the same rich field boundary. This typed
				// conversion stops compiling if their output contracts diverge.
				jobs = append(jobs, recruitee.Job(job))
			}
		}
		if err != nil {
			return RichDiscovery{Response: response}, &DiscoveryError{Kind: "invalid_inventory"}
		}
		for _, job := range jobs {
			title, titleErr := executor.CoerceText(job.Title)
			description, descriptionErr := executor.CoerceText(job.Description)
			if titleErr != nil || descriptionErr != nil {
				return RichDiscovery{Response: response}, &DiscoveryError{Kind: "invalid_inventory"}
			}
			var locationType any
			if job.JobLocationType != nil {
				locationType = *job.JobLocationType
			}
			result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: description, Locations: job.Locations, DatePosted: job.DatePosted, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: locationType})
		}
		return result, nil
	}
	if profile.Provider != "lever" {
		return result, &DiscoveryError{Kind: "invalid_configuration"}
	}
	totalBytes := 0
	for skip := 0; skip <= lever.MaxJobs; skip += lever.BatchSize {
		endpoint := strings.TrimSuffix(profile.Endpoint, "skip=0") + "skip=" + strconv.Itoa(skip)
		var body []byte
		var response *GreenhouseResponse
		var err error
		for attempt := 0; attempt < 3; attempt++ {
			body, response, err = richPage(ctx, client, endpoint, true)
			if response != nil {
				totalBytes += response.bytes
			}
			if totalBytes > 64<<20 {
				return RichDiscovery{}, &DiscoveryError{Kind: "body_limit"}
			}
			if response != nil && response.reserved {
				return RichDiscovery{Response: response}, err
			}
			if ctx.Err() != nil {
				return RichDiscovery{}, ctx.Err()
			}
			if err == nil {
				var raw []json.RawMessage
				if json.Unmarshal(body, &raw) != nil || raw == nil {
					err = &DiscoveryError{Kind: "invalid_inventory"}
				}
			}
			if err == nil {
				break
			}
			if response != nil && response.status != 200 && response.status != 408 && response.status != 425 && response.status != 429 && !(response.status >= 500 && response.status < 600) {
				break
			}
			if attempt < 2 {
				if waitErr := pauseRich(ctx, time.Duration(float64(time.Duration(500*time.Millisecond)<<attempt)*(0.5+rand.Float64()))); waitErr != nil {
					return RichDiscovery{}, waitErr
				}
			}
		}
		if err != nil {
			if skip == 0 {
				return RichDiscovery{Response: response}, err
			}
			// A later-page 404 is a failed complete inventory, not board disappearance.
			return RichDiscovery{}, err
		}
		jobs, count, err := lever.ParsePage(body)
		if err != nil {
			return RichDiscovery{}, &DiscoveryError{Kind: "invalid_inventory"}
		}
		result.Response = response
		for _, job := range jobs {
			title, err := executor.CoerceText(job.Title)
			if err != nil {
				return RichDiscovery{}, &DiscoveryError{Kind: "invalid_inventory"}
			}
			result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: job.Description, Locations: job.Locations, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType})
		}
		if count < lever.BatchSize {
			return result, nil
		}
		if len(result.Jobs) >= lever.MaxJobs {
			result.Truncated = true
			return result, nil
		}
		if err := pauseRich(ctx, 500*time.Millisecond); err != nil {
			return RichDiscovery{}, err
		}
	}
	return RichDiscovery{}, &DiscoveryError{Kind: "page_limit"}
}
