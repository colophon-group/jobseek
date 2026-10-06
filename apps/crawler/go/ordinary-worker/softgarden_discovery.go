package worker

import (
	"context"
	"errors"
	"net/http"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverSoftgardenInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.SoftgardenOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || config["monitor_needs_browser"] != "0" || p.Provider != "softgarden" || p.Profile != "softgarden.inline-urls/v1" || p.Endpoint != o.ListingURL() {
		return result, queue.ErrConfiguration
	}
	// The reference listing follows redirects and accepts every successful 2xx.
	raw, response, err := fetchProviderStatusResource(ctx, client, o, o.ListingURL(), nil, nil, 64<<20, nil, true)
	result.Response = response
	if err != nil {
		var failure *DiscoveryError
		if errors.As(err, &failure) && failure.Kind == "provider_gone" {
			err = &DiscoveryError{Kind: "http_status", Status: failure.Status}
		}
		return result, err
	}
	urls, truncated, err := api.SoftgardenListing(string(raw), o)
	if err != nil {
		return result, err
	}
	result.Truncated = truncated
	for _, source := range urls {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	return result, nil
}
