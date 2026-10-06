package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"math/rand/v2"
	"net/http"
	"time"
	"unicode/utf8"
)

func fetchFifthListing(ctx context.Context, client *http.Client, scope providerResourceScope, source string, retryStatuses map[int]bool, maxChars int, requireNonempty bool, wait func(context.Context, time.Duration) error) (string, *GreenhouseResponse, error) {
	var response *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, observed, e := fetchProviderResource(ctx, client, scope, source, nil, nil, 64<<20)
		response = observed
		if ctx.Err() != nil {
			return "", response, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			return "", response, e
		}
		status := 0
		if response != nil {
			status = response.status
		}
		if e == nil && status == 200 {
			text := jsonld.DecodeDocument(raw, "")
			if utf8.RuneCountInString(text) >= maxChars {
				return "", response, &DiscoveryError{Kind: "body_limit"}
			}
			if text != "" || !requireNonempty {
				return text, response, nil
			}
		}
		retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599 || retryStatuses[status]
		if !retry || attempt == 2 {
			return "", response, &DiscoveryError{Kind: "listing_failed", Status: status}
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if e = wait(ctx, delay); e != nil {
			return "", response, e
		}
	}
	return "", response, api.ErrInventory
}
func discoverADPInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverADPInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverADPInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, e := api.ADPOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "adp" || p.Profile != "adp.search-items/v1" || p.Endpoint != b.SearchURL(1) {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	headers := http.Header{"Accept": []string{"application/json"}, "X-Requested-With": []string{"XMLHttpRequest"}, "X-Forwarded-Host": []string{"workforcenow.adp.com"}}
	expected := int64(-1)
	rawCount := 0
	invalid, duplicates := 0, 0
	countChanged := false
	seen := map[string]bool{}
	for start := 1; start <= 50000; start += 20 {
		total, rows, response, e := fetchADPSearchPage(ctx, &sealed, b, start, headers, wait)
		out.Response = response
		if e != nil {
			if start == 1 && response != nil && (response.status == 404 || response.status == 410) {
				return out, &DiscoveryError{Kind: "provider_gone", Status: response.status}
			}
			return RichDiscovery{Response: response}, e
		}
		if expected < 0 {
			expected = total
		}
		if expected != total {
			countChanged = true
		}
		for _, row := range rows {
			rawCount++
			fields := api.ADPJobFields(row, b)
			if fields == nil {
				invalid++
				continue
			}
			source := fields["url"].(string)
			if seen[source] {
				duplicates++
				continue
			}
			seen[source] = true
			job, e := secondaryRichJob(fields)
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			out.Jobs = append(out.Jobs, job)
		}
		if int64(rawCount) >= expected || len(rows) == 0 || rawCount >= 50000 {
			out.Truncated = invalid > 0 || duplicates > 0 || int64(rawCount) != expected || countChanged
			return out, nil
		}
	}
	out.Truncated = true
	return out, nil
}
func discoverCornerstoneInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverCornerstoneInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverCornerstoneInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, e := api.CornerstoneOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "cornerstone" || p.Profile != "cornerstone.search-items/v1" || p.Endpoint != b.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	bootstrap := func() (api.CornerstoneContext, error) {
		for attempt := 0; attempt < 2; attempt++ {
			page, response, e := fetchFifthListing(ctx, &sealed, b, b.ListingURL(), map[int]bool{202: true, 401: true, 403: true}, 1_000_000, true, wait)
			out.Response = response
			if e != nil {
				if response != nil && (response.status == 404 || response.status == 410) {
					return api.CornerstoneContext{}, &DiscoveryError{Kind: "provider_gone", Status: response.status}
				}
				return api.CornerstoneContext{}, e
			}
			classified, classificationErr := dom.ClassifyDocument(page, dom.Object{}, b.ListingURL())
			if classificationErr != nil || classified["classification"] == "challenge" {
				return api.CornerstoneContext{}, &DiscoveryError{Kind: "bot_challenge"}
			}
			c, e := api.CornerstoneExtractContext(page, b)
			if !errors.Is(e, api.ErrCornerstoneContextMissing) || attempt == 1 {
				return c, e
			}
			if e = wait(ctx, 500*time.Millisecond); e != nil {
				return api.CornerstoneContext{}, e
			}
		}
		return api.CornerstoneContext{}, api.ErrInventory
	}
	c, e := bootstrap()
	if e != nil {
		return out, e
	}
	fetch := func(page int) (int64, []any, error) {
		for refresh := 0; refresh < 2; refresh++ {
			payload, e := api.CornerstoneSearchPayload(b, c, page)
			if e != nil {
				return 0, nil, e
			}
			d, response, e := fetchSecondaryJSONPage(ctx, &sealed, c, c.SearchURL(), payload, c.Headers, nil, wait)
			out.Response = response
			if e == nil {
				return api.CornerstonePage(d)
			}
			if refresh == 0 && response != nil && (response.status == 401 || response.status == 403) {
				c, e = bootstrap()
				if e != nil {
					return 0, nil, e
				}
				continue
			}
			return 0, nil, e
		}
		return 0, nil, api.ErrInventory
	}
	expected := int64(-1)
	rawCount := 0
	invalid, duplicates := 0, 0
	seen := map[string]bool{}
	for page := 1; page <= 500; page++ {
		total, rows, e := fetch(page)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		if expected < 0 {
			expected = total
		}
		if total != expected {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		if len(rows) == 0 && int64(rawCount) < total {
			total, rows, e = fetch(page)
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			if total != expected || len(rows) == 0 {
				return RichDiscovery{Response: out.Response}, api.ErrInventory
			}
		}
		if len(rows) > 100 || int64(rawCount+len(rows)) > total {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		for _, row := range rows {
			rawCount++
			fields := api.CornerstoneJobFields(row, b, c.CultureName)
			if fields == nil {
				invalid++
				continue
			}
			source := fields["url"].(string)
			if seen[source] {
				duplicates++
				continue
			}
			seen[source] = true
			job, e := secondaryRichJob(fields)
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			out.Jobs = append(out.Jobs, job)
		}
		if int64(rawCount) >= total || rawCount >= 50000 {
			if total > 0 && len(seen) == 0 {
				return RichDiscovery{Response: out.Response}, api.ErrInventory
			}
			out.Truncated = invalid > 0 || duplicates > 0 || int64(rawCount) != total
			return out, nil
		}
	}
	out.Truncated = true
	return out, nil
}
func discoverPaylocityInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverPaylocityInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverPaylocityInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.PaylocityOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "paylocity" || p.Profile != "paylocity.embedded-items/v1" || p.Endpoint != o.Listing {
		return out, queue.ErrConfiguration
	}
	sealed := *client

	page, response, e := fetchFifthListing(ctx, &sealed, o, o.Listing, nil, 5_000_001, false, wait)
	out.Response = response
	if e != nil {
		if response != nil && (response.status == 404 || response.status == 410) {
			return out, &DiscoveryError{Kind: "provider_gone", Status: response.status}
		}
		return out, e
	}
	fields, e := api.PaylocityPage(page, o)
	if e != nil {
		return out, e
	}
	for _, field := range fields {
		job, e := secondaryRichJob(field)
		if e != nil {
			return RichDiscovery{Response: response}, e
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}

func fetchADPSearchPage(ctx context.Context, client *http.Client, b api.ADPBoard, start int, headers http.Header, wait func(context.Context, time.Duration) error) (int64, []any, *GreenhouseResponse, error) {
	var response *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, r, e := fetchProviderStatusResource(ctx, client, b, b.SearchURL(start), nil, headers, 64<<20, nil, true)
		response = r
		if ctx.Err() != nil {
			return 0, nil, response, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			return 0, nil, response, e
		}
		if e == nil {
			d, err := api.Decode(raw)
			if err == nil {
				total, rows, err := api.ADPPage(d, start)
				if err == nil {
					return total, rows, response, nil
				}
			}
		}
		status := 0
		if response != nil {
			status = response.status
		}
		retry := status == 0 || status >= 200 && status < 300 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
		if !retry || attempt == 2 {
			return 0, nil, response, &DiscoveryError{Kind: "adp_page_failed", Status: status}
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if e = wait(ctx, delay); e != nil {
			return 0, nil, response, e
		}
	}
	return 0, nil, response, api.ErrInventory
}
