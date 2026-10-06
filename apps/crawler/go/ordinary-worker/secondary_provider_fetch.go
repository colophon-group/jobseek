package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func secondaryRichJob(fields map[string]any) (RichMonitorJob, error) {
	title, err := executor.CoerceText(fields["title"])
	if err != nil {
		return RichMonitorJob{}, err
	}
	description, err := executor.CoerceText(fields["description"])
	if err != nil {
		return RichMonitorJob{}, err
	}
	if description != nil {
		description, err = enrichment.NormalizeDescriptionHTML(*description)
		if err != nil {
			return RichMonitorJob{}, err
		}
	}
	locations, _ := fields["locations"].([]string)
	metadata, _ := fields["metadata"].(map[string]any)
	extras, _ := fields["extras"].(map[string]any)
	source, _ := fields["url"].(string)
	return RichMonitorJob{URL: source, Title: title, Description: description, Locations: locations, Metadata: metadata, Extras: extras, DatePosted: fields["date_posted"], Language: fields["language"], EmploymentType: fields["employment_type"], JobLocationType: fields["job_location_type"]}, nil
}

// UKG and BambooHR use the existing shared three-attempt JSON-page contract.
// Each caller retains its exact endpoint, POST body, extra retry statuses and
// source-bound observation; no successful prefix becomes a complete inventory.
func fetchSecondaryJSONPage(ctx context.Context, client *http.Client, scope providerResourceScope, endpoint string, body []byte, headers http.Header, retryStatuses map[int]bool, wait func(context.Context, time.Duration) error) (*api.Document, *GreenhouseResponse, error) {
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, response, err := fetchProviderResource(ctx, client, scope, endpoint, body, headers, 64<<20)
		observed = response
		if ctx.Err() != nil {
			return nil, observed, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(err, &reserved) {
			return nil, observed, err
		}
		status := 0
		if response != nil {
			status = response.status
		}
		if err == nil && status == 200 {
			d, err := api.Decode(raw)
			if err == nil {
				if _, ok := d.Value.(map[string]any); ok {
					return d, observed, nil
				}
			}
		} else if status != 0 && status != 200 && status != 408 && status != 425 && status != 429 && (status < 500 || status > 599) && !retryStatuses[status] {
			return nil, observed, &DiscoveryError{Kind: "http_status", Status: status}
		}
		if attempt+1 == 3 {
			break
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if err := wait(ctx, delay); err != nil {
			return nil, observed, err
		}
	}
	return nil, observed, &DiscoveryError{Kind: "json_page_failed"}
}
