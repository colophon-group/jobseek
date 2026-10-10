package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"
)

func FetchSuccessFactorsRMKHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.SuccessFactorsRMKOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || !queue.RSSRMKProfile(p.Profile) || p.Endpoint != o.BoardURL || config["monitor_needs_browser"] != "0" {
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
	attempts := 3
	if queue.ProfileRequiresProxy(p.Profile) {
		attempts = 6
	}
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		var body []byte
		if r.Method == "POST" {
			body = []byte(r.Body)
		} else if r.Method != "GET" {
			return nil, queue.ErrConfiguration
		}
		limit := int64(2_000_000)
		if r.Method == "POST" {
			limit = 5_000_000
		}
		for attempt := 0; attempt < attempts; attempt++ {
			raw, response, err := fetchProviderStatusResource(ctx, &sealed, o, r.URL, body, r.Headers, limit, nil, true)
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
			if err == nil && status >= 200 && status < 300 && len(raw) > 0 {
				return raw, nil
			}
			retry := status == 0 || status >= 200 && status < 300 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt+1 == attempts {
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
	found, err := api.DiscoverSuccessFactorsRMK(ctx, o, fetch)
	if err != nil {
		return out, err
	}
	out.Truncated = found.Truncated
	for _, j := range found.Jobs {
		title, e := executor.CoerceText(j.Title)
		if e != nil {
			return out, e
		}
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: j.URL, Title: title, Locations: j.Locations, DatePosted: j.DatePosted, EmploymentType: j.EmploymentType, Metadata: j.Metadata, Extras: j.Extras})
	}
	return out, nil
}
