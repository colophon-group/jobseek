package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"math/rand/v2"
	"net/http"
	"sort"
	"time"
	"unicode/utf8"
)

func discoverJobviteInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverJobviteInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverJobviteInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.JobviteOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "jobvite" || p.Profile != "jobvite.listing-urls/v1" || p.Endpoint != o.Endpoint {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(endpoint string, terminal bool) (api.JobvitePage, error) {
		var body []byte
		var response *GreenhouseResponse
		var err error
		for attempt := 0; attempt < 3; attempt++ {
			body, response, err = fetchProviderStatusResource(ctx, &sealed, o, endpoint, nil, nil, 64<<20, nil)
			out.Response = response
			if ctx.Err() != nil {
				return api.JobvitePage{}, ctx.Err()
			}
			var reserved *policy.Reservation
			if errors.As(err, &reserved) {
				return api.JobvitePage{}, err
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if err == nil && status == 200 {
				text := jsonld.DecodeDocument(body, "")
				if utf8.RuneCountInString(text) > 5_000_000 {
					return api.JobvitePage{}, &DiscoveryError{Kind: "body_limit"}
				}
				if text != "" {
					return api.JobviteListing(text, o, endpoint)
				}
			}
			retry := status == 0 || status == 200 || status == 202 || status == 401 || status == 403 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt == 2 {
				if terminal && response != nil && (status == 404 || status == 410 || api.JobviteInvalidRedirect(response.location)) {
					response.providerDisabled = api.JobviteInvalidRedirect(response.location)
					return api.JobvitePage{}, &DiscoveryError{Kind: "provider_gone", Status: status}
				}
				return api.JobvitePage{}, &DiscoveryError{Kind: "listing_failed", Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if e := wait(ctx, delay); e != nil {
				return api.JobvitePage{}, e
			}
		}
		return api.JobvitePage{}, &DiscoveryError{Kind: "listing_failed"}
	}
	pages := 0
	expand := func(initial api.JobvitePage) ([]string, error) {
		jobs := map[string]bool{}
		for _, s := range initial.Jobs {
			jobs[s] = true
		}
		categories := map[string]bool{}
		for _, s := range initial.Searches {
			v, ok := o.SearchFromURL(s)
			if !ok || v.Page != 0 {
				return nil, api.ErrInventory
			}
			categories[v.Category] = true
		}
		ordered := []string{}
		for s := range categories {
			ordered = append(ordered, s)
		}
		sort.Strings(ordered)
		for _, category := range ordered {
			seen := map[string]bool{}
			total, size := -1, 0
			for n := 0; n < 5000; n++ {
				if pages >= 5000 {
					return nil, api.ErrInventory
				}
				endpoint := o.SearchURL(api.JobviteSearch{Category: category, Page: n})
				page, e := fetch(endpoint, false)
				pages++
				if e != nil {
					return nil, e
				}
				if !page.HasRange || page.Total < 1 || page.Start < 1 || page.End < page.Start {
					return nil, api.ErrInventory
				}
				if total < 0 {
					if page.Start != 1 {
						return nil, api.ErrInventory
					}
					total, size = page.Total, page.End
				}
				// Bounding the advertised integer prevents multiplication overflow and a
				// page counter from making an incomplete prefix appear authoritative.
				if total > 50000 || size > 50000 {
					return nil, api.ErrInventory
				}
				start := n*size + 1
				end := min(start+size-1, total)
				if page.Total != total || page.Start != start || page.End != end || len(page.Jobs) != end-start+1 {
					return nil, api.ErrInventory
				}
				for _, s := range page.Jobs {
					if seen[s] {
						return nil, api.ErrInventory
					}
					seen[s] = true
					jobs[s] = true
				}
				if len(jobs) > 50000 {
					return nil, api.ErrInventory
				}
				if end == total {
					if len(seen) != total {
						return nil, api.ErrInventory
					}
					break
				}
				next := o.SearchURL(api.JobviteSearch{Category: category, Page: n + 1})
				found := false
				for _, s := range page.Searches {
					if s == next {
						found = true
					}
				}
				if !found {
					return nil, api.ErrInventory
				}
			}
		}
		urls := []string{}
		for s := range jobs {
			urls = append(urls, s)
		}
		sort.Strings(urls)
		return urls, nil
	}
	first, e := fetch(o.Endpoint, true)
	if e != nil {
		return out, e
	}
	urls, e := expand(first)
	if e != nil {
		return out, e
	}
	if len(urls) == 0 && len(first.Listings) > 0 {
		for _, candidate := range first.Listings[:min(6, len(first.Listings))] {
			page, e := fetch(candidate, false)
			if e != nil {
				if out.Response != nil && (out.Response.status == 404 || out.Response.status == 410) {
					continue
				}
				return out, e
			}
			urls, e = expand(page)
			if e != nil {
				return out, e
			}
			if len(urls) > 0 {
				break
			}
		}
	}
	for _, s := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: s, URLOnly: true})
	}
	return out, nil
}
