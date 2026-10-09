package apisniffer

import (
	"context"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"sync"
	"unicode/utf8"
)

// PageUp emits only validated pages. The owner commits each page and applies
// absence handling only when the complete inventory has been proved.
func DiscoverPageUp(ctx context.Context, o PortalHTTPOptions, fetch SmallProviderFetch, emit func([]map[string]any) error) ([]map[string]any, error) {
	if o.Provider != "pageup" || fetch == nil {
		return nil, ErrOptions
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	var expected *int
	for page := 1; page <= 100; page++ {
		resource := o.PageUp.PageURL(page, 500)
		body, err := fetch(ctx, Request{Method: "GET", URL: resource})
		if err != nil {
			return nil, err
		}
		parsed, err := ParsePageUpListing(body, resource, o.PageUp, page, 500, expected)
		if err != nil {
			return nil, err
		}
		batch, total, next := parsed.Jobs, parsed.Total, parsed.HasNext
		expected = &total
		for _, job := range batch {
			identity, ok := job["url"].(string)
			if !ok || seen[identity] {
				return nil, ErrInventory
			}
			seen[identity] = true
		}
		if emit != nil && (len(batch) > 0 || !next) {
			if err := emit(batch); err != nil {
				return nil, err
			}
		}
		jobs = append(jobs, batch...)
		if !next {
			if len(jobs) != total {
				return nil, ErrInventory
			}
			return jobs, nil
		}
	}
	return nil, ErrInventory
}

func DiscoverKeka(ctx context.Context, o PortalHTTPOptions, fetch SmallProviderFetch, normalize SmallDescriptionNormalizer) ([]map[string]any, error) {
	if o.Provider != "keka" || fetch == nil || normalize == nil {
		return nil, ErrOptions
	}
	body, err := fetch(ctx, Request{Method: "GET", URL: o.Keka.ListingURL()})
	if err != nil {
		return nil, err
	}
	identifier := KekaBootstrapIdentifier(body)
	if utf8.RuneCount(body) > 500_000 || identifier == "" || o.Keka.Identifier != "" && o.Keka.Identifier != identifier {
		return nil, ErrInventory
	}
	board := o.Keka
	board.Identifier = identifier
	body, err = fetch(ctx, Request{Method: "GET", URL: board.JobsURL(), Headers: http.Header{"Accept": {"application/json"}}})
	if err != nil {
		return nil, err
	}
	if utf8.RuneCount(body) > 25_000_000 {
		return nil, ErrInventory
	}
	d, err := Decode(body)
	if err != nil {
		return nil, err
	}
	rows, ok := d.Value.([]any)
	if !ok || len(rows) > 50_000 {
		return nil, ErrInventory
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	for _, value := range rows {
		row, ok := value.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		job, err := KekaJobFields(row, board.ListingURL(), normalize)
		if err != nil {
			return nil, err
		}
		identity, _ := job["url"].(string)
		if identity == "" || seen[identity] {
			return nil, ErrInventory
		}
		seen[identity] = true
		jobs = append(jobs, job)
	}
	return jobs, nil
}

func DiscoverInfoniqa(ctx context.Context, o PortalHTTPOptions, fetch SmallProviderFetch) ([]map[string]any, error) {
	if o.Provider != "infoniqa" || fetch == nil {
		return nil, ErrOptions
	}
	body, err := fetch(ctx, Request{Method: "GET", URL: o.BoardURL, Headers: http.Header{"Accept": {"text/html,application/xhtml+xml"}}})
	if err != nil {
		return nil, err
	}
	shell, err := ParseInfoniqaShell(body, o.BoardURL, o.Employer)
	if err != nil {
		return nil, err
	}
	post := func(resource, body, accept string) ([]byte, error) {
		return fetch(ctx, Request{Method: "POST", URL: resource, Body: body, Headers: http.Header{
			"Accept": {accept}, "Content-Type": {"application/x-www-form-urlencoded; charset=UTF-8"},
			"Origin": {o.Origin}, "Referer": {o.BoardURL}, "X-Requested-With": {"XMLHttpRequest"},
		}})
	}
	csrf := url.QueryEscape(shell.CSRF)
	body, err = post(o.Listing+"?search=true", "j=jobexchange&_csrf="+csrf, "text/html, */*; q=0.01")
	if err != nil {
		return nil, err
	}
	search, err := ParseInfoniqaSearch(body, o.BoardURL)
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, identity := range search.URLs {
		seen[identity] = true
	}
	for page := 0; ; page++ {
		body, err = post(o.Listing, "hasNextJobOffers=true&_csrf="+csrf, "application/json, text/javascript, */*; q=0.01")
		if err != nil {
			return nil, err
		}
		next := strings.TrimSpace(string(body))
		if next != "true" && next != "false" {
			return nil, ErrInventory
		}
		if next == "false" {
			break
		}
		if page >= 2000 {
			return nil, ErrInventory
		}
		body, err = post(o.Listing, "showNextJobOffers=true&j=jobexchange&_csrf="+csrf, "text/html, */*; q=0.01")
		if err != nil {
			return nil, err
		}
		identities, err := ParseInfoniqaPage(body, o.BoardURL)
		if err != nil {
			return nil, err
		}
		for _, identity := range identities {
			if seen[identity] {
				return nil, ErrInventory
			}
			seen[identity] = true
		}
		if len(seen) > search.Total {
			return nil, ErrInventory
		}
	}
	if len(seen) != search.Total {
		return nil, ErrInventory
	}
	urls := []string{}
	for identity := range seen {
		urls = append(urls, identity)
	}
	sort.Strings(urls)
	jobs := []map[string]any{}
	for _, identity := range urls {
		jobs = append(jobs, map[string]any{"url": identity})
	}
	return jobs, nil
}

func DiscoverTurboHire(ctx context.Context, o PortalHTTPOptions, fetch SmallProviderFetch, normalize SmallDescriptionNormalizer) ([]map[string]any, error) {
	if o.Provider != "turbohire" || fetch == nil || normalize == nil {
		return nil, ErrOptions
	}
	board, _ := url.Parse(o.BoardURL)
	headers := http.Header{"Accept": {"application/json, text/plain, */*"}, "Origin": {board.Scheme + "://" + board.Host}, "Referer": {o.BoardURL}}
	object := func(ctx context.Context, request Request) (*Document, map[string]any, error) {
		body, err := fetch(ctx, request)
		if err != nil {
			return nil, nil, err
		}
		d, err := Decode(body)
		if err != nil {
			return nil, nil, err
		}
		row, ok := d.Value.(map[string]any)
		if !ok {
			return nil, nil, ErrInventory
		}
		return d, row, nil
	}
	_, tokenReply, err := object(ctx, Request{Method: "GET", URL: o.Listing, Headers: headers})
	if err != nil {
		return nil, err
	}
	token, ok := tokenReply["access_token"].(string)
	if !ok || token == "" {
		return nil, ErrInventory
	}
	headers.Set("Authorization", "Bearer "+token)
	filters := map[string]any{"SortByV2": map[string]any{"Key": "PostedDate", "Order": 2}, "Keyword": "", "Department": "", "CustomFields": map[string]any{}}
	for _, key := range []string{"BunitIds", "Experience", "JobTypes", "JobTypeV2", "Locations", "CreatedDate", "Compensation", "Skills", "ClientIds"} {
		filters[key] = map[string]any{"Value": nil, "FilterType": 0}
	}
	body, err := portalMarshal(filters)
	if err != nil {
		return nil, err
	}
	_, listing, err := object(ctx, Request{Method: "POST", URL: "https://thapi.azurewebsites.net/api/careerpagev2/filteredjobs?" + url.Values{"orgId": {o.Organization}, "pageType": {"0"}}.Encode(), Body: body, Headers: headers})
	if err != nil {
		return nil, err
	}
	total, err := smallInt(listing["Total"], false)
	rows, ok := listing["Result"].([]any)
	if err != nil || !ok || total > 50_000 || len(rows) != total {
		return nil, ErrInventory
	}
	jobs := make([]map[string]any, len(rows))
	for start := 0; start < len(rows); start += 10 {
		child, cancel := context.WithCancel(ctx)
		var wg sync.WaitGroup
		var mu sync.Mutex
		var first error
		for index := start; index < min(start+10, len(rows)); index++ {
			wg.Add(1)
			go func(index int) {
				defer wg.Done()
				row, ok := rows[index].(map[string]any)
				publicID := smallText(row["JobIdObfuscated"])
				var job map[string]any
				var err error
				if !ok || publicID == "" {
					err = ErrInventory
				} else {
					_, detail, failure := object(child, Request{Method: "GET", URL: "https://thapi.azurewebsites.net/api/publicjobs?" + url.Values{"jobId": {publicID}, "fieldVisibility": {"0"}}.Encode(), Headers: headers})
					err = failure
					if err == nil {
						if !reflect.DeepEqual(detail["JobId"], row["JobId"]) {
							err = ErrInventory
						} else {
							job, err = TurboHireJobFields(detail, o.Origin, normalize)
						}
					}
				}
				if err != nil {
					mu.Lock()
					if first == nil {
						first = err
					}
					mu.Unlock()
					cancel()
					return
				}
				jobs[index] = job
			}(index)
		}
		wg.Wait()
		cancel()
		if first != nil {
			return nil, first
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
	}
	return jobs, nil
}
