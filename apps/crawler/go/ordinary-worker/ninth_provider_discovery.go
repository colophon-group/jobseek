package worker

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func normalizeNinthFields(fields map[string]any, raw map[string]any, provider string) error {
	if provider == "hirehive" || provider == "welcometothejungle" {
		value, _ := fields["job_location_type"].(string)
		fields["job_location_type"] = nil
		if value = enrichment.NormalizeJobLocationType(value); value != "" {
			fields["job_location_type"] = value
		}
	}
	if provider == "hirehive" && fields["base_salary"] == nil {
		if text, ok := raw["salary"].(string); ok && strings.TrimSpace(text) != "" {
			result, err := enrichment.Salary(text, nil)
			if err != nil {
				return err
			}
			if parsed := result.Parsed; parsed != nil {
				fields["base_salary"] = map[string]any{"currency": parsed.Currency, "min": parsed.Min, "max": parsed.Max, "unit": parsed.Unit}
			}
		}
	}
	return nil
}

func FetchNinthProvidersHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.NinthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	previous := sealed.CheckRedirect
	sealed.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		if len(via) >= 20 || !o.ResourceMatches(r.URL.String()) {
			return queue.ErrConfiguration
		}
		if previous != nil {
			return previous(r, via)
		}
		return nil
	}
	appendFields := func(document *api.Document, row any) (bool, error) {
		fields, err := document.NinthProviderJobFields(row, o)
		if err != nil || fields == nil {
			return false, err
		}
		raw, _ := row.(map[string]any)
		if err := normalizeNinthFields(fields, raw, o.Provider); err != nil {
			return false, err
		}
		job, err := secondaryRichJob(fields)
		if err != nil {
			return false, err
		}
		if o.Provider == "beehire" {
			job.LocalizedTitles, job.LocalizationLocales = api.BeehireLocalizationVectors(fields)
		}
		out.Jobs = append(out.Jobs, job)
		return true, nil
	}
	fetch := func(source string, body []byte, headers http.Header) (*api.Document, error) {
		raw, response, err := fetchProviderStatusResource(ctx, &sealed, o, source, body, headers, 64<<20, nil, true)
		out.Response = response
		if err != nil {
			return nil, err
		}
		return api.Decode(raw)
	}
	switch o.Provider {
	case "ycombinator":
		raw, response, err := fetchProviderStatusResource(ctx, &sealed, o, o.ListingURL(), nil, nil, 64<<20, nil, true)
		out.Response = response
		if err != nil {
			return out, ninthStatusError(err, response, false)
		}
		urls, err := api.YCombinatorListingURLs(string(raw), o)
		if err != nil {
			return out, err
		}
		for _, source := range urls {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
		}
		return out, nil
	case "computrabajo":
		urls, total, response, err := fetchComputrabajoInventory(ctx, &sealed, o, wait)
		out.Response = response
		if err != nil {
			return out, err
		}
		for _, source := range urls {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
		}
		out.Truncated = total > 10000
		return out, nil
	case "beehire":
		document, err := fetch(o.ListingURL(), nil, http.Header{"Accept": {"application/json"}, "Referer": {o.Origin + "/career/" + o.Slug}})
		if err != nil {
			return out, ninthStatusError(err, out.Response, out.Response != nil && out.Response.status == 404)
		}
		root, _ := document.Value.(map[string]any)
		rows, ok := root["campaigns"].([]any)
		if !ok {
			return out, api.ErrInventory
		}
		invalid, seen := 0, map[string]bool{}
		for _, row := range rows {
			added, err := appendFields(document, row)
			if err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
			if !added {
				invalid++
				continue
			}
			last := out.Jobs[len(out.Jobs)-1]
			if seen[last.URL] {
				invalid++
				out.Jobs = out.Jobs[:len(out.Jobs)-1]
			} else {
				seen[last.URL] = true
			}
		}
		if len(rows) > 0 && len(out.Jobs) == 0 {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		out.Truncated = invalid > 0 || len(rows) > 50000
		if len(out.Jobs) > 50000 {
			out.Jobs = out.Jobs[:50000]
		}
		return out, nil
	case "hirehive":
		for page := 1; page <= 500; page++ {
			document, response, err := fetchSecondaryJSONPage(ctx, &sealed, o, o.PageURL(page), nil, nil, nil, wait)
			out.Response = response
			if err != nil {
				return RichDiscovery{Response: response}, ninthStatusError(err, response, page == 1)
			}
			root, _ := document.Value.(map[string]any)
			rows, ok := root["items"].([]any)
			meta, _ := root["meta"].(map[string]any)
			next, nextOK := meta["has_next_page"].(bool)
			if !ok || !nextOK {
				return RichDiscovery{Response: response}, api.ErrInventory
			}
			for _, row := range rows {
				if _, err := appendFields(document, row); err != nil {
					return RichDiscovery{Response: response}, err
				}
			}
			if len(out.Jobs) > 50000 || next && (len(out.Jobs) >= 50000 || page == 500) {
				out.Truncated = true
				if len(out.Jobs) > 50000 {
					out.Jobs = out.Jobs[:50000]
				}
				return out, nil
			}
			if !next {
				return out, nil
			}
		}
	case "welcometothejungle":
		organization := o.Organization
		if organization == "" {
			document, err := fetch(o.ListingURL(), nil, nil)
			if err != nil {
				return out, ninthStatusError(err, out.Response, false)
			}
			root, _ := document.Value.(map[string]any)
			object, _ := root["organization"].(map[string]any)
			organization, _ = object["slug"].(string)
			if organization == "" {
				return out, api.ErrInventory
			}
		}
		// Only the existing anonymous browser's search-only index is queried.
		params := url.Values{"hitsPerPage": {"1000"}, "page": {"0"}, "filters": {"organization.slug:" + organization}}
		body, err := json.Marshal(map[string]any{"requests": []any{map[string]any{"indexName": "wk_cms_jobs_production", "params": params.Encode()}}})
		if err != nil {
			return out, err
		}
		document, err := fetch("https://csekhvms53-dsn.algolia.net/1/indexes/*/queries", body, ninthWTTJHeaders())
		if err != nil {
			return out, err
		}
		root, _ := document.Value.(map[string]any)
		results, _ := root["results"].([]any)
		if len(results) == 0 {
			return out, api.ErrInventory
		}
		result, _ := results[0].(map[string]any)
		hits, ok := result["hits"].([]any)
		if !ok {
			return out, api.ErrInventory
		}
		summaries, err := api.WTTJSummaries(hits, o.Slug)
		if err != nil {
			return out, err
		}
		if n, ok := result["nbHits"].(json.Number); ok {
			total, err := strconv.Atoi(string(n))
			if err == nil {
				out.Truncated = total > 1000
			}
		}
		details, observed, err := fetchWTTJDetails(ctx, &sealed, o, summaries)
		if err != nil {
			return RichDiscovery{Response: observed}, err
		}
		for _, document := range details {
			if document == nil {
				continue
			}
			root, _ := document.Value.(map[string]any)
			row, ok := root["job"].(map[string]any)
			if !ok {
				return RichDiscovery{Response: out.Response}, api.ErrInventory
			}
			if _, err := appendFields(document, row); err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
		}
		return out, nil
	}
	return RichDiscovery{Response: out.Response}, api.ErrInventory
}

func ninthStatusError(err error, response *GreenhouseResponse, gone bool) error {
	var d *DiscoveryError
	if errors.As(err, &d) && response != nil && (response.status == 404 || response.status == 410) {
		if gone {
			return &DiscoveryError{Kind: "provider_gone", Status: response.status}
		}
		return &DiscoveryError{Kind: "http_status", Status: response.status}
	}
	return err
}

func fetchWTTJDetails(ctx context.Context, client *http.Client, o api.NinthProviderOptions, rows []map[string]any) ([]*api.Document, *GreenhouseResponse, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	out := make([]*api.Document, len(rows))
	semaphore := make(chan struct{}, 10)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var failure error
	var failedResponse *GreenhouseResponse
loop:
	for index, row := range rows {
		select {
		case semaphore <- struct{}{}:
		case <-ctx.Done():
			break loop
		}
		wg.Add(1)
		go func(index int, row map[string]any) {
			defer wg.Done()
			defer func() { <-semaphore }()
			slug, _ := row["slug"].(string)
			raw, response, err := fetchProviderStatusResource(ctx, client, o, o.ListingURL()+"/jobs/"+slug, nil, nil, 64<<20, nil, true)
			if response != nil && (response.status == 404 || response.status == 410) {
				return
			}
			var document *api.Document
			if err == nil {
				document, err = api.Decode(raw)
			}
			if err != nil {
				mu.Lock()
				if failure == nil {
					failure, failedResponse = err, response
					cancel()
				}
				mu.Unlock()
				return
			}
			out[index] = document
		}(index, row)
	}
	wg.Wait()
	if failure != nil {
		return nil, failedResponse, failure
	}
	return out, nil, ctx.Err()
}

func fetchComputrabajoInventory(ctx context.Context, client *http.Client, o api.NinthProviderOptions, wait func(context.Context, time.Duration) error) ([]string, int, *GreenhouseResponse, error) {
	var response *GreenhouseResponse
	for snapshot := 0; snapshot < 2; snapshot++ {
		seen, total, changed := map[string]bool{}, -1, false
		for page := 1; page <= 500; page++ {
			used, retries := map[int]int{}, 0
			var raw []byte
			var err error
			for {
				raw, response, err = fetchProviderStatusResource(ctx, client, o, o.PageURL(page), nil, nil, 64<<20, nil, true)
				limit, status := 0, 0
				if response != nil {
					status = response.status
				}
				if status == 403 {
					limit = 1
				}
				if status == 429 {
					limit = 2
				}
				if limit == 0 || used[status] >= limit {
					break
				}
				used[status]++
				retries++
				delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(retries-1)) * (0.5 + rand.Float64()))
				if err := wait(ctx, delay); err != nil {
					return nil, 0, response, err
				}
			}
			if err != nil {
				return nil, 0, response, ninthStatusError(err, response, true)
			}
			urls, count, err := api.ParseComputrabajoListing(raw, o, page)
			if err != nil {
				return nil, 0, response, err
			}
			if total == -1 {
				total = count
			} else if total != count {
				changed = true
				break
			}
			for _, source := range urls {
				if seen[source] {
					changed = true
					break
				}
				seen[source] = true
			}
			if changed {
				break
			}
			target := min(total, 10000)
			if page*20 >= target {
				if len(seen) != target {
					return nil, 0, response, api.ErrInventory
				}
				urls := []string{}
				for source := range seen {
					urls = append(urls, source)
				}
				sort.Strings(urls)
				return urls, total, response, nil
			}
		}
		if !changed || snapshot == 1 {
			return nil, 0, response, api.ErrInventory
		}
		if err := wait(ctx, time.Second); err != nil {
			return nil, 0, response, err
		}
	}
	return nil, 0, response, api.ErrInventory
}

func ninthWTTJHeaders() http.Header {
	return http.Header{"X-Algolia-Application-Id": {"CSEKHVMS53"}, "X-Algolia-Api-Key": {"4bd8f6215d0cc52b26430765769e65a0"}, "Content-Type": {"application/x-www-form-urlencoded"}, "Origin": {"https://www.welcometothejungle.com"}, "Referer": {"https://www.welcometothejungle.com/"}}
}
