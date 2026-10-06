package worker

import (
	"context"
	"errors"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var bambooDescriptionTags = regexp.MustCompile(`<[^>]+>`)

func discoverBambooHRInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverBambooHRInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverBambooHRInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, err := api.BambooHROptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "bamboohr" || p.Profile != "bamboohr.careers-list/v1" || p.Endpoint != b.ListingURL() {
		return result, queue.ErrConfiguration
	}
	op := *client
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	d, response, err := fetchSecondaryJSONPage(ctx, &op, b, b.ListingURL(), nil, nil, nil, wait)
	result.Response = response
	if err != nil {
		if response != nil {
			gone := response.status == 404 || response.status == 410
			if response.status == 301 || response.status == 302 || response.status == 303 || response.status == 307 || response.status == 308 {
				base, _ := url.Parse(b.ListingURL())
				target, e := base.Parse(response.location)
				gone = e == nil && response.location != "" && (strings.EqualFold(target.Hostname(), "bamboohr.com") || strings.EqualFold(target.Hostname(), "www.bamboohr.com"))
			}
			if gone {
				response.providerDisabled = response.status != 404 && response.status != 410
				err = &DiscoveryError{Kind: "provider_gone", Status: response.status}
			}
		}
		return result, err
	}
	fields, truncated, err := api.BambooHRListing(d, b)
	if err != nil {
		return RichDiscovery{Response: response}, err
	}
	result.Truncated = truncated
	if b.DescriptionInclude != "" {
		if len(fields) > 500 {
			return RichDiscovery{Response: response}, api.ErrInventory
		}
		pattern, err := dom.CompileURLPattern(b.DescriptionInclude)
		if err != nil {
			return RichDiscovery{Response: response}, err
		}
		filtered := make([]map[string]any, 0, len(fields))
		for start := 0; start < len(fields); start += 10 {
			end := min(start+10, len(fields))
			window := fields[start:end]
			selected := make([]bool, len(window))
			failures := make([]error, len(window))
			observations := make([]*GreenhouseResponse, len(window))
			var group sync.WaitGroup
			for i, field := range window {
				group.Add(1)
				go func(i int, field map[string]any) {
					defer group.Done()
					id := field["metadata"].(map[string]any)["job_id"].(string)
					detail, observed, e := fetchSecondaryJSONPage(ctx, &op, b, b.DetailURL(id), nil, nil, nil, wait)
					observations[i] = observed
					if e != nil {
						failures[i] = e
						return
					}
					payload, _ := detail.Value.(map[string]any)
					root, _ := payload["result"].(map[string]any)
					opening, _ := root["jobOpening"].(map[string]any)
					description, ok := opening["description"].(string)
					if !ok {
						failures[i] = api.ErrInventory
						return
					}
					text := strings.Join(strings.Fields(html.UnescapeString(bambooDescriptionTags.ReplaceAllString(description, " "))), " ")
					match, e := pattern.MatchString(text)
					if e != nil {
						failures[i] = e
						return
					}
					selected[i] = match
					if match {
						field["description"] = description
					}
				}(i, field)
			}
			group.Wait()
			// A reservation on any completed required resource outranks sibling failure.
			for i, e := range failures {
				var reserved *policy.Reservation
				if errors.As(e, &reserved) {
					return RichDiscovery{Response: observations[i]}, e
				}
			}
			for i, e := range failures {
				if e != nil {
					return RichDiscovery{Response: observations[i]}, e
				}
			}
			for i, field := range window {
				if selected[i] {
					filtered = append(filtered, field)
				}
			}
		}
		fields = filtered
	}
	for _, field := range fields {
		job, err := secondaryRichJob(field)
		if err != nil {
			return RichDiscovery{Response: response}, err
		}
		result.Jobs = append(result.Jobs, job)
	}
	return result, nil
}
