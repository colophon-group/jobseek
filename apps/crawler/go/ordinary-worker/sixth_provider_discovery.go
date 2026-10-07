package worker

import (
	"context"
	"net/http"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverManatalInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.ManatalOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || p.Provider != "manatal" || p.Profile != "manatal.career-items/v1" || p.Endpoint != o.ListingURL(1) || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	jobs, truncated, e := api.DiscoverManatal(ctx, o, func(ctx context.Context, source string) (*api.Document, error) {
		// Manatal's original path performs one GET per page and accepts2xx.
		raw, response, e := fetchProviderStatusResource(ctx, client, o, source, nil, nil, 64<<20, nil, true)
		out.Response = response
		if e != nil {
			return nil, e
		}
		return api.Decode(raw)
	})
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	out.Truncated = truncated
	for _, fields := range jobs {
		job, e := secondaryRichJob(fields)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}

func discoverHRMOSInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverHRMOSInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverHRMOSInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.HRMOSOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || p.Provider != "hrmos" || p.Profile != "hrmos.listing-urls/v1" || p.Endpoint != o.ListingURL(1) || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	urls, truncated, e := api.DiscoverHRMOS(ctx, o, func(ctx context.Context, source string) (string, error) {
		body, response, e := fetchFifthListing(ctx, &sealed, o, source, map[int]bool{202: true, 401: true, 403: true}, 64<<20, true, wait)
		out.Response = response
		if e != nil {
			if source == o.ListingURL(1) && response != nil && (response.status == 404 || response.status == 410) {
				return "", &DiscoveryError{Kind: "provider_gone", Status: response.status}
			}
			return "", e
		}
		// The Python bounded text reader retains the first2M characters and
		// marks a listing at that limit as incomplete.
		runes := []rune(body)
		if len(runes) > 2000000 {
			body = string(runes[:2000000])
		}
		classified, e := dom.ClassifyDocument(body, dom.Object{}, source)
		if e != nil || classified["classification"] == "challenge" {
			return "", &DiscoveryError{Kind: "bot_challenge"}
		}
		return body, nil
	}, pythonJoinURL)
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	out.Truncated = truncated
	for _, source := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	return out, nil
}

func discoverRecruiterboxInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverRecruiterboxInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverRecruiterboxInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.RecruiterboxOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || p.Provider != "recruiterbox" || p.Profile != "recruiterbox.listing-urls/v1" || p.Endpoint != o.PageURL(1) || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	urls, truncated, e := api.DiscoverRecruiterbox(ctx, o, func(ctx context.Context, source string) (string, bool, error) {
		body, response, e := fetchFifthListing(ctx, &sealed, o, source, map[int]bool{202: true, 401: true, 403: true}, 64<<20, true, wait)
		out.Response = response
		if e != nil {
			if response != nil && (response.status == 404 || response.status == 410) {
				if source == o.PageURL(1) {
					return "", false, &DiscoveryError{Kind: "provider_gone", Status: response.status}
				}
				return "", true, nil
			}
			return "", false, e
		}
		runes := []rune(body)
		if len(runes) > 2000001 {
			body = string(runes[:2000001])
		}
		if api.RecruiterboxInactive(body) {
			response.providerDisabled = true
			return "", false, &DiscoveryError{Kind: "provider_gone", Status: 200}
		}
		classified, e := dom.ClassifyDocument(body, dom.Object{}, source)
		if e != nil || classified["classification"] == "challenge" {
			return "", false, &DiscoveryError{Kind: "bot_challenge"}
		}
		return body, false, nil
	}, pythonJoinURL)
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	out.Truncated = truncated
	for _, source := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	return out, nil
}
func discoverJobCloudInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverJobCloudInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverJobCloudInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || p.Provider != "jobs_ch" || p.Profile != "jobs_ch.company-urls/v1" || p.Endpoint != o.SearchURL(1) || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	urls, e := api.DiscoverJobCloud(ctx, o, func(ctx context.Context, source string) (*api.Document, error) {
		d, response, e := fetchSecondaryJSONPage(ctx, client, o, source, nil, nil, nil, wait)
		out.Response = response
		return d, e
	})
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	for _, source := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	return out, nil
}
