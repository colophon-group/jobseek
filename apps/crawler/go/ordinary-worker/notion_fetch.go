package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func notionFetch(client *http.Client, o api.NotionOptions, observe func(*GreenhouseResponse), wait func(context.Context, time.Duration) error) api.NotionFetch {
	return func(parent context.Context, r api.Request) (*api.Document, int, error) {
		if client == nil || wait == nil || r.Method != "POST" || !o.ResourceMatches(r.URL) || len(r.Body) > 1<<20 {
			return nil, 0, queue.ErrConfiguration
		}
		status := 0
		for attempt := 0; attempt < 3; attempt++ {
			// A transport failure must not inherit an earlier HTTP status and
			// authorize the root-only HTTP 500 fallback.
			status = 0
			requestCtx, cancel := context.WithTimeout(parent, 15*time.Second)
			raw, response, err := fetchProviderResource(requestCtx, client, o, r.URL, []byte(r.Body), r.Headers, 5<<20)
			cancel()
			if response != nil {
				status = response.status
				if observe != nil {
					observe(response)
				}
			}
			if parent.Err() != nil {
				return nil, status, parent.Err()
			}
			var reserved *policy.Reservation
			if errors.As(err, &reserved) {
				return nil, status, err
			}
			if err == nil && status == 200 {
				d, failure := api.Decode(raw)
				if failure == nil {
					if _, ok := d.Value.(map[string]any); ok {
						return d, status, nil
					}
				}
				err = api.ErrInventory
			}
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt == 2 {
				if err != nil {
					return nil, status, err
				}
				return nil, status, &DiscoveryError{Kind: "notion_api_failed", Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if err := wait(parent, delay); err != nil {
				return nil, status, err
			}
		}
		return nil, status, api.ErrInventory
	}
}

func FetchNotionHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.NotionOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || p.Provider != "notion" || p.Profile != "notion.public-urls/v1" || config["monitor_needs_browser"] != "0" || p.Endpoint != "https://"+o.Subdomain+".notion.site/api/v3/getPublicPageData" {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	urls, err := api.DiscoverNotion(ctx, o, notionFetch(&sealed, o, func(r *GreenhouseResponse) { out.Response = r }, wait))
	if err != nil {
		return out, err
	}
	for _, source := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	return out, nil
}

func fetchNotionDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	o, err := api.NotionOptionsFromMetadata(p.SourceURL, "{}")
	if err != nil || p.Profile != "notion.public-detail/v1" || p.Endpoint != "https://"+o.Subdomain+".notion.site/api/v3/loadPageChunk" {
		return nil, nil, queue.ErrConfiguration
	}
	d, err := api.LoadNotionChunk(ctx, o, o.Hint, notionFetch(client, o, nil, pauseRich))
	if err != nil {
		var reservation *policy.Reservation
		if errors.As(err, &reservation) {
			return nil, reservation, nil
		}
		return nil, nil, err
	}
	mapping := map[string]string{}
	if values, ok := p.EmbeddedConfig["property_map"].(map[string]any); ok {
		for key, value := range values {
			text, ok := value.(string)
			if !ok {
				return nil, nil, queue.ErrConfiguration
			}
			mapping[key] = text
		}
	}
	fields, err := api.NotionDetailFields(d, o.Hint, mapping)
	return fields, nil, err
}
