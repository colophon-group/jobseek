package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchUmantisHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.UmantisOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "umantis" || (p.Profile != "umantis.listing-urls/v1" && p.Profile != "umantis.proxy-listing-urls/v1") || p.Endpoint != o.Listing {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	previous := sealed.CheckRedirect
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		if len(via) >= 20 || !o.ResourceMatches(r.URL.String()) {
			return queue.ErrConfiguration
		}
		if previous != nil {
			return previous(r, via)
		}
		return nil
	}
	rows, truncated, err := api.DiscoverUmantis(ctx, o, func(ctx context.Context, resource string, tail bool) ([]byte, string, error) {
		attempts := 1
		if tail {
			attempts = 3
		}
		for attempt := 0; attempt < attempts; attempt++ {
			raw, response, err := fetchProviderResource(ctx, &sealed, o, resource, nil, api.UmantisRequest(resource).Headers, 5<<20)
			if response != nil {
				out.Response = response
			}
			if ctx.Err() != nil {
				return nil, "", ctx.Err()
			}
			var reserved *policy.Reservation
			if errors.As(err, &reserved) {
				return nil, "", err
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if err == nil && status == 200 {
				return []byte(jsonld.DecodeDocument(raw, "")), response.finalURL, nil
			}
			if tail && (status == 404 || status == 410) {
				return nil, resource, nil
			}
			retry := status == 0 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt+1 == attempts {
				if err != nil {
					return nil, "", err
				}
				return nil, "", &DiscoveryError{Kind: "umantis_page_failed", Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if err := wait(ctx, delay); err != nil {
				return nil, "", err
			}
		}
		return nil, "", api.ErrInventory
	})
	if err != nil {
		return out, err
	}
	out.Truncated = truncated
	md, err := api.DecodeInlineMetadata(config["metadata"])
	if err != nil {
		return out, err
	}
	scraper, _ := md["scraper_config"].(map[string]any)
	enrich, _ := scraper["enrich"].([]any)
	rich := len(enrich) > 0
	for _, row := range rows {
		job := RichMonitorJob{URL: row.URL, URLOnly: !rich}
		if rich {
			title := row.Title
			job.Title = &title
			if row.Location != "" {
				job.Locations = append(job.Locations, row.Location)
			}
			if row.EmploymentType != "" {
				job.EmploymentType = row.EmploymentType
			}
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
