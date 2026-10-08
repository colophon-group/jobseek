package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchTenthProvidersHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.TenthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	previous := sealed.CheckRedirect
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		if o.Provider == "universia" {
			return http.ErrUseLastResponse
		}
		if len(via) >= 20 || !o.ResourceMatches(r.URL.String()) {
			return queue.ErrConfiguration
		}
		if previous != nil {
			return previous(r, via)
		}
		return nil
	}
	fetch := func(ctx context.Context, request api.Request) ([]byte, error) {
		var body []byte
		if request.Method == "POST" {
			body = []byte(request.Body)
		} else if request.Method != "GET" {
			return nil, queue.ErrConfiguration
		}
		limit := int64(64 << 20)
		if o.Provider == "intervieweb" {
			limit = 5000000
		}
		if o.Provider == "universia" {
			limit = 20000000
		}
		for attempt := 0; attempt < 3; attempt++ {
			raw, response, err := fetchProviderResource(ctx, &sealed, o, request.URL, body, request.Headers, limit)
			if response != nil {
				out.Response = response
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			var reservation *policy.Reservation
			if errors.As(err, &reservation) {
				return nil, err
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if err == nil && status == 200 && len(raw) > 0 {
				return raw, nil
			}
			extra := status == 202 || status == 403 || o.Provider == "intervieweb" && status == 401
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599 || extra
			if !retry || attempt == 2 {
				if err != nil {
					return nil, err
				}
				return nil, &DiscoveryError{Kind: "public_page_failed", Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if err := wait(ctx, delay); err != nil {
				return nil, err
			}
		}
		return nil, api.ErrInventory
	}
	var fields []map[string]any
	switch o.Provider {
	case "intervieweb":
		var urls []string
		for attempt := 0; attempt < 2; attempt++ {
			urls, err = api.DiscoverInterviewebSnapshot(ctx, o, fetch)
			if !errors.Is(err, api.ErrInterviewebSnapshotChanged) || attempt == 1 {
				break
			}
			if err = wait(ctx, time.Second); err != nil {
				return out, err
			}
		}
		if err != nil {
			return out, err
		}
		out.Truncated = len(urls) >= 50000
		for _, source := range urls {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
		}
		return out, nil
	case "typify":
		fields, out.Truncated, err = api.DiscoverTypify(ctx, o, fetch)
	case "universia":
		fields, out.Truncated, err = api.DiscoverUniversia(ctx, o, fetch)
	case "talentreef":
		fields, out.Truncated, err = api.DiscoverTalentReef(ctx, o, fetch)
	default:
		return out, queue.ErrConfiguration
	}
	if err != nil {
		return out, err
	}
	for _, field := range fields {
		if o.Provider == "universia" {
			value, _ := field["employment_type"].(string)
			field["employment_type"] = nil
			if normalized := executor.ScraperEmploymentType(&value); normalized != nil {
				field["employment_type"] = *normalized
			}
			value, _ = field["job_location_type"].(string)
			field["job_location_type"] = nil
			if value = enrichment.NormalizeJobLocationType(value); value != "" {
				field["job_location_type"] = value
			}
		}
		job, err := secondaryRichJob(field)
		if err != nil {
			return out, err
		}
		job.SourceIdentity, _ = field["source_identity"].(string)
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
