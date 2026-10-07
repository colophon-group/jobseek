package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchEighthProvidersHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.EighthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	previous := sealed.CheckRedirect
	sealed.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		if len(via) >= 10 || !o.ResourceMatches(r.URL.String()) {
			return queue.ErrConfiguration
		}
		if previous != nil {
			return previous(r, via)
		}
		return nil
	}
	appendFields := func(fields map[string]any) error {
		if value, ok := fields["job_location_type"].(string); ok {
			fields["job_location_type"] = nil
			if value = enrichment.NormalizeJobLocationType(value); value != "" {
				fields["job_location_type"] = value
			}
		}
		job, err := secondaryRichJob(fields)
		if err != nil {
			return err
		}
		job.SourceIdentity, _ = fields["source_identity"].(string)
		out.Jobs = append(out.Jobs, job)
		return nil
	}
	switch o.Provider {
	case "earcu":
		for index, source := range o.FeedURLs {
			body, response, err := fetchFifthListing(ctx, &sealed, o, source, map[int]bool{403: true}, 50<<20, false, wait)
			out.Response = response
			if err != nil {
				// Only an absent autodetection candidate falls through. A malformed
				// or denied feed never publishes a successful prefix or empty set.
				if response != nil && response.status == 404 && index+1 < len(o.FeedURLs) {
					continue
				}
				return RichDiscovery{Response: response}, err
			}
			fields, err := api.ParseEArcuFeed([]byte(body), source)
			if err != nil {
				return RichDiscovery{Response: response}, err
			}
			for _, fields := range fields {
				if err := appendFields(fields); err != nil {
					return RichDiscovery{Response: response}, err
				}
			}
			return out, nil
		}
		return out, api.ErrInventory
	case "cvwarehouse":
		fetch := func(source string) ([]byte, error) {
			body, response, err := fetchFifthListing(ctx, &sealed, o, source, nil, 30000000, true, wait)
			out.Response = response
			return []byte(body), err
		}
		landing, err := fetch(o.BoardURL)
		if err != nil {
			return out, err
		}
		folded := strings.ToLower(string(landing))
		if !strings.Contains(folded, "cvwarehouse") || !strings.Contains(folded, "section=") && !strings.Contains(folded, "data-jobdetail-job-id") {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		section, expected := o.Section, o.Advertised
		if section == "" {
			var count int
			section, count, err = api.CVWarehouseSection(landing)
			if err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
			expected = &count
		}
		primary, err := o.SectionURL(o.BoardURL, section)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		body, err := fetch(primary)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		locales, err := api.CVWarehouseLocales(body, primary)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		seen := map[string]bool{}
		for index, source := range locales {
			if index != 0 {
				body, err = fetch(source)
				if err != nil {
					return RichDiscovery{Response: out.Response}, err
				}
			}
			fields, err := api.ParseCVWarehousePage(body, source)
			if err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
			for _, fields := range fields {
				metadata := fields["metadata"].(map[string]any)
				id := metadata["job_id"].(string)
				if seen[id] {
					continue
				}
				seen[id] = true
				if err := appendFields(fields); err != nil {
					return RichDiscovery{Response: out.Response}, err
				}
			}
		}
		if len(out.Jobs) > 10000 || expected != nil && *expected != len(out.Jobs) {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		return out, nil
	case "woowa":
		rows, response, truncated, err := fetchWoowaListings(ctx, &sealed, o, wait)
		out.Response = response
		if err != nil {
			return out, err
		}
		details, responses, err := fetchWoowaDetails(ctx, &sealed, o, rows, wait)
		if err != nil {
			return RichDiscovery{Response: responses}, err
		}
		for index, detail := range details {
			fields, err := api.WoowaJobFields(o, rows[index], detail)
			if err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
			if err := appendFields(fields); err != nil {
				return RichDiscovery{Response: out.Response}, err
			}
		}
		out.Truncated = truncated
		return out, nil
	}
	return out, queue.ErrConfiguration
}

func woowaData(document *api.Document) (map[string]any, error) {
	if document == nil {
		return nil, api.ErrInventory
	}
	root, ok := document.Value.(map[string]any)
	data, dataOK := root["data"].(map[string]any)
	if !ok || root["code"] != "2000" || !dataOK {
		return nil, api.ErrInventory
	}
	return data, nil
}

func fetchWoowaListings(ctx context.Context, client *http.Client, o api.EighthProviderOptions, wait func(context.Context, time.Duration) error) ([]map[string]any, *GreenhouseResponse, bool, error) {
	out, seen, expected := []map[string]any{}, map[string]bool{}, -1
	var response *GreenhouseResponse
	for page := 0; page <= 50000; page++ {
		document, observed, err := fetchSecondaryJSONPage(ctx, client, o, o.WoowaPageURL(page), nil, nil, nil, wait)
		response = observed
		if err != nil {
			if page == 0 && response != nil && (response.status == 404 || response.status == 410) {
				err = &DiscoveryError{Kind: "provider_gone", Status: response.status}
			}
			return nil, response, false, err
		}
		data, err := woowaData(document)
		if err != nil {
			return nil, response, false, err
		}
		rows, ok := data["list"].([]any)
		number, numberOK := data["totalSize"].(json.Number)
		total, parseErr := strconv.Atoi(string(number))
		if !ok || !numberOK || parseErr != nil || total < 0 || expected != -1 && total != expected {
			return nil, response, false, api.ErrInventory
		}
		expected = total
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				return nil, response, false, api.ErrInventory
			}
			id, _ := row["recruitNumber"].(string)
			if id == "" || !o.ResourceMatches(o.ListingURL()+"/"+id) || seen[id] {
				return nil, response, false, api.ErrInventory
			}
			seen[id] = true
			out = append(out, row)
		}
		if len(out) >= expected {
			if len(out) != expected {
				return nil, response, false, api.ErrInventory
			}
			return out, response, false, nil
		}
		if len(rows) == 0 {
			return nil, response, false, api.ErrInventory
		}
		if len(out) >= 50000 {
			return out[:50000], response, true, nil
		}
	}
	return nil, response, false, api.ErrInventory
}

func fetchWoowaDetails(ctx context.Context, client *http.Client, o api.EighthProviderOptions, rows []map[string]any, wait func(context.Context, time.Duration) error) ([]map[string]any, *GreenhouseResponse, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	details := make([]map[string]any, len(rows))
	responses := make([]*GreenhouseResponse, len(rows))
	errorsByRow := make([]error, len(rows))
	slots, group := make(chan struct{}, 20), sync.WaitGroup{}
launch:
	for index, row := range rows {
		if ctx.Err() != nil {
			break
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			break launch
		}
		group.Add(1)
		go func(index int, row map[string]any) {
			defer group.Done()
			defer func() { <-slots }()
			id := row["recruitNumber"].(string)
			document, response, err := fetchSecondaryJSONPage(ctx, client, o, o.ListingURL()+"/"+id, nil, nil, nil, wait)
			if err == nil {
				details[index], err = woowaData(document)
				if err == nil && details[index]["recruitNumber"] != id {
					err = api.ErrInventory
				}
			}
			responses[index], errorsByRow[index] = response, err
			if err != nil {
				cancel()
			}
		}(index, row)
	}
	group.Wait()
	// A positive publisher denial outranks sibling cancellation and is retained
	// as the complete cycle's outcome, never as a partial successful inventory.
	for index, err := range errorsByRow {
		var reservation *policy.Reservation
		if errors.As(err, &reservation) {
			return nil, responses[index], err
		}
	}
	for index, err := range errorsByRow {
		if err != nil && !errors.Is(err, context.Canceled) {
			return nil, responses[index], err
		}
	}
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	for _, detail := range details {
		if detail == nil {
			return nil, nil, api.ErrInventory
		}
	}
	return details, nil, nil
}
