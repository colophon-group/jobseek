package worker

import (
	"context"
	"net/http"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	successfactors "github.com/colophon-group/jobseek/apps/crawler/go/successfactors-rss-monitor"
)

func discoverSuccessFactorsRich(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	observed := &rssRichClient{client: client, profile: profile}
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	summary, err := successfactors.Fetch(ctx, observed, profile.Endpoint, func(job successfactors.Job) error {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: job.Title, Description: job.Description, Locations: job.Locations, DatePosted: job.DatePosted, Metadata: job.Metadata})
		return nil
	})
	if ctx.Err() != nil {
		return RichDiscovery{}, ctx.Err()
	}
	if err != nil {
		if observed.response != nil && observed.response.reserved {
			return RichDiscovery{Response: observed.response}, &DiscoveryError{Kind: "publisher_reserved", Status: observed.response.status}
		}
		// Emitted jobs before an XML or body error never establish a full listing.
		return RichDiscovery{}, &DiscoveryError{Kind: "feed_failed"}
	}
	result.Truncated, result.Response = summary.Truncated, observed.response
	return result, nil
}
