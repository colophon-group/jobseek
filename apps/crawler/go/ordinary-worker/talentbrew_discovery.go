package worker

import (
	"context"
	"net/http"
	"net/http/cookiejar"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func FetchTalentBrewHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.TalentBrewOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || p.Provider != "talentbrew" || p.Profile != "talentbrew.listing-urls/v1" || p.Endpoint != o.BoardURL || config["monitor_needs_browser"] != "0" {
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
	missingLater := false
	urls, err := api.DiscoverTalentBrew(ctx, o, func(ctx context.Context, endpoint string, maxChars int) (string, bool, error) {
		body, response, err := fetchFifthListing(ctx, &sealed, o, endpoint, map[int]bool{202: true, 401: true, 403: true}, maxChars, false, wait)
		if response != nil {
			out.Response = response
		}
		if err != nil && response != nil && (response.status == 404 || response.status == 410) && !response.reserved {
			if endpoint != o.BoardURL {
				missingLater = true
			}
			return "", false, nil
		}
		return body, err == nil, err
	})
	if err != nil {
		return out, err
	}
	out.Truncated = missingLater || len(urls) >= 50000
	for _, raw := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: raw, URLOnly: true})
	}
	return out, nil
}
