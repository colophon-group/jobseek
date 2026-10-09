package apisniffer

import (
	"context"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var inploiSegment = regexp.MustCompile(`\\?"segment_ids\\?"\s*,\s*\\?"segment\\?"\s*,\s*\\?"([0-9]+)\\?"`)

func InploiSearchRequest(key, segment string, page, size int) Request {
	endpoint := InploiAPIURL + "?filters%5Bsegment_ids%5D%5B0%5D=" + url.QueryEscape(segment) + "&query=&page=" + strconv.Itoa(page) + "&per_page=" + strconv.Itoa(size)
	return Request{Method: "GET", URL: endpoint, Headers: http.Header{"Accept": []string{"application/json"}, "X-Publishable-Key": []string{key}}}
}

func inploiPage(body []byte, page, size int) (*Document, []any, int, int, error) {
	d, e := Decode(body)
	if e != nil {
		return nil, nil, 0, 0, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, nil, 0, 0, ErrInventory
	}
	rows, ok := m["data"].([]any)
	p, valid := m["pagination"].(map[string]any)
	if !ok || !valid {
		return nil, nil, 0, 0, ErrInventory
	}
	total, e := smallInt(p["total"], false)
	current, ce := smallInt(p["current_page"], false)
	last, le := smallInt(p["last_page"], false)
	if e != nil || ce != nil || le != nil || current != page || last < 1 || last != max(1, (total+size-1)/size) || len(rows) != min(size, max(total-(page-1)*size, 0)) {
		return nil, nil, 0, 0, ErrInventory
	}
	return d, rows, total, last, nil
}

func DiscoverInploi(ctx context.Context, o FinalHTTPProviderOptions, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil || o.Provider != "inploi" || o.PageSize < 1 {
		return nil, false, ErrOptions
	}
	key, segment := o.APIKey, o.SegmentID
	if key == "" || segment == "" {
		base, _ := url.Parse(o.BoardURL)
		candidates := []string{o.BoardURL}
		search := base.Scheme + "://" + base.Host + "/search"
		if search != o.BoardURL {
			candidates = append(candidates, search)
		}
		found := false
		for _, source := range candidates {
			body, e := fetch(ctx, Request{Method: "GET", URL: source})
			if e != nil {
				return nil, false, e
			}
			if !strings.Contains(strings.ToLower(string(body)), "inploi") {
				continue
			}
			k := inploiPublicKey.FindString(string(body))
			match := inploiSegment.FindSubmatch(body)
			if k == "" || len(match) != 2 {
				continue
			}
			body, e = fetch(ctx, InploiSearchRequest(k, string(match[1]), 1, 1))
			if e != nil {
				return nil, false, e
			}
			if _, _, _, _, e = inploiPage(body, 1, 1); e != nil {
				continue
			}
			key, segment, found = k, string(match[1]), true
			break
		}
		if !found {
			return nil, false, ErrInventory
		}
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	total, last, rawSeen, invalid, duplicates := -1, 0, 0, 0, 0
	for page := 1; ; page++ {
		if e := ctx.Err(); e != nil {
			return nil, false, e
		}
		body, e := fetch(ctx, InploiSearchRequest(key, segment, page, o.PageSize))
		if e != nil {
			return nil, false, e
		}
		d, rows, count, lastPage, e := inploiPage(body, page, o.PageSize)
		if e != nil {
			return nil, false, e
		}
		if total < 0 {
			total, last = count, lastPage
		} else if total != count || last != lastPage {
			return nil, false, ErrInventory
		}
		rawSeen += len(rows)
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				invalid++
				continue
			}
			field, e := InploiJobFields(d, row, o.BoardURL, o.JobURLTemplate)
			if e != nil {
				return nil, false, e
			}
			if field == nil {
				invalid++
				continue
			}
			source := field["url"].(string)
			if seen[source] {
				duplicates++
				continue
			}
			seen[source] = true
			jobs = append(jobs, field)
		}
		if page >= last || total > 50000 && rawSeen+o.PageSize > 50000 {
			break
		}
	}
	if total > 0 && len(jobs) == 0 {
		return nil, false, ErrInventory
	}
	return jobs, invalid > 0 || duplicates > 0 || rawSeen != total || total > 50000, nil
}
