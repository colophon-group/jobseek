package worker

import (
	"context"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

var jazzHRJobID = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func discoverJazzHRInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if client == nil || p.Provider != "jazzhr" || p.Profile != "jazzhr.listing-urls/v1" || p.Endpoint != "https://"+p.Token+".applytojob.com/apply/jobs" {
		return result, queue.ErrConfiguration
	}
	body, response, err := fetchListingPage(ctx, client, dom.ICIMSRequest{URL: p.Endpoint}, 5_000_000)
	result.Response = response
	if err != nil {
		return result, err
	}
	return parseJazzHRInventory(ctx, result, p, string(body))
}

func parseJazzHRInventory(ctx context.Context, result RichDiscovery, p queue.GreenhouseMonitorProfile, source string) (RichDiscovery, error) {
	classification, err := dom.ClassifyDocument(source, dom.Object{}, p.Endpoint)
	if err != nil || classification["classification"] == "challenge" || !strings.Contains(source, `id="job_listings_wrapper"`) {
		return result, &DiscoveryError{Kind: "invalid_inventory"}
	}
	hrefs, err := dom.ListingHrefs(source, "")
	if err != nil {
		return result, err
	}
	urls := map[string]bool{}
	matcher := regexp.MustCompile(`(?i)^https://` + regexp.QuoteMeta(p.Token) + `\.applytojob\.com/apply/jobs/details/[A-Za-z0-9_-]+(?:[/?#]|$)`)
	for _, href := range hrefs {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		raw, ok := joinPythonURL(p.Endpoint, href)
		if !ok || !matcher.MatchString(raw) {
			continue
		}
		u, err := url.Parse(raw)
		if err != nil || u.Scheme != "https" || !strings.EqualFold(u.Hostname(), p.Token+".applytojob.com") || u.User != nil || u.Port() != "" && u.Port() != "443" {
			continue
		}
		parts := []string{}
		for _, part := range strings.Split(u.Path, "/") {
			if part != "" {
				parts = append(parts, part)
			}
		}
		if len(parts) != 4 || parts[0] != "apply" || parts[1] != "jobs" || parts[2] != "details" || !jazzHRJobID.MatchString(parts[3]) {
			continue
		}
		urls["https://"+p.Token+".applytojob.com/apply/jobs/details/"+parts[3]] = true
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	result.Truncated = utf8.RuneCountInString(source) >= 5_000_000 || len(ordered) > 50_000
	if len(ordered) > 50_000 {
		ordered = ordered[:50_000]
	}
	for _, raw := range ordered {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}
