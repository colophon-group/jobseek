package worker

import (
	"context"
	"net/http"
	"net/http/cookiejar"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverAmazonInventory(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string, yield func([]RichMonitorJob) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.AmazonOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || verified == nil || yield == nil || profile.Provider != "amazon" || profile.Profile != api.AmazonProfile || profile.Endpoint != o.InitialURL() {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, _ = cookiejar.New(nil)
	client.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		if len(via) > 5 || !o.ResourceMatches(r.URL.String()) {
			return queue.ErrConfiguration
		}
		return nil
	}
	observation := &lastHTTPObservation{}
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		if previous := observation.latest(); previous != nil && previous.reserved {
			return nil, &DiscoveryError{Kind: "publisher_reserved"}
		}
		body, _, response, err := fetchLastHTTPOnce(ctx, &client, o, r, 64<<20)
		observation.observe(response)
		return body, err
	}
	result.Truncated, err = api.DiscoverAmazon(ctx, o, fetch, func(fields []map[string]any) error {
		jobs := make([]RichMonitorJob, 0, len(fields))
		for _, field := range fields {
			job, err := secondaryRichJob(field)
			if err != nil {
				return err
			}
			if salary := field["base_salary"]; salary != nil {
				job.Extras = map[string]any{"base_salary": salary}
			}
			jobs = append(jobs, job)
		}
		return yield(jobs)
	})
	result.Response = observation.latest()
	return result, err
}
