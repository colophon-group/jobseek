package apisniffer

import (
	"encoding/json"
	"reflect"
	"strings"
)

// Fixed provider requests use the existing held browser controller. Lifecycle
// metadata stays bound to the caller's fingerprint and never becomes a request.
func NativeBrowserOptions(provider, board, raw string) (BrowserReplayOptions, string, error) {
	if provider == "accenture" {
		_, options, err := AccentureBrowserOptions(board, raw)
		return options, board, err
	}
	if provider == "brassring" {
		_, options, err := BrassRingBrowserOptions(board, raw)
		return options, board, err
	}
	o := BrowserReplayOptions{Wait: "load", TimeoutMS: 20000, ResponseBodyLimit: 16 << 20}
	m, e := DecodeInlineMetadata(raw)
	if e != nil {
		return o, "", e
	}
	filtered := map[string]any{}
	for k, v := range m {
		switch k {
		case "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		case "host", "company_id", "wait", "timeout":
			if provider != "darwinbox" {
				return o, "", ErrOptions
			}
			filtered[k] = v
		default:
			return o, "", ErrOptions
		}
	}
	if provider == "darwinbox" {
		body, _ := json.Marshal(filtered)
		b, e := DarwinboxOptionsFromMetadata(board, string(body))
		if e != nil {
			return o, "", e
		}
		o.Wait = "domcontentloaded"
		o.TimeoutMS = 60000
		if s, ok := filtered["wait"].(string); ok {
			o.Wait = s
		}
		if n, ok := integer(filtered["timeout"]); ok {
			o.TimeoutMS = uint64(n)
		}
		r := b.PageRequest(1)
		o.Inventory = Options{Endpoint: r.URL, Method: r.Method, Body: r.Body, Headers: r.Headers}
		return o, b.ListingURL(), nil
	}
	if provider == "bytedance" {
		p, e := ByteDanceOptionsFromURL(board)
		if e != nil {
			return o, "", e
		}
		r := p.PageRequest(0, nil)
		if p.PartitionCategories {
			r = Request{Method: "GET", URL: ByteDanceFilterURL, Headers: p.Headers()}
		}
		o.Inventory = Options{Endpoint: r.URL, Method: r.Method, Body: r.Body, Headers: r.Headers}
		return o, board, nil
	}
	return o, "", ErrOptions
}
func NativeBrowserRequestMatches(provider, board, metadata string, r Request) bool {
	if provider == "brassring" || provider == "accenture" {
		// The fixed search/sort/next driver owns these UI requests. No caller
		// may manufacture them through the generic fetch conversation.
		return false
	}
	_, _, e := NativeBrowserOptions(provider, board, metadata)
	if e != nil {
		return false
	}
	if provider == "darwinbox" {
		b, e := DarwinboxBoardFromURL(board)
		if e != nil || r.Method != "POST" || r.URL != b.JobsURL() {
			return false
		}
		var m struct {
			Company string `json:"companyId"`
			Page    int    `json:"page"`
			Sort    string `json:"sort_option"`
			Limit   int    `json:"limit"`
		}
		if json.Unmarshal([]byte(r.Body), &m) != nil || m.Page < 1 || m.Page > 500 {
			return false
		}
		expected := b.PageRequest(m.Page)
		return r.Body == expected.Body && reflect.DeepEqual(r.Headers, expected.Headers)
	}
	p, e := ByteDanceOptionsFromURL(board)
	if e != nil || !p.RequestMatches(r) || !reflect.DeepEqual(r.Headers, p.Headers()) {
		return false
	}
	if r.Method == "GET" {
		return true
	}
	var body struct {
		Offset     int      `json:"offset"`
		Categories []string `json:"job_category_id_list"`
	}
	if json.Unmarshal([]byte(r.Body), &body) != nil || body.Offset < 0 || body.Offset >= 10000 || body.Offset%1000 != 0 || len(body.Categories) > 1 || (!p.PartitionCategories && len(body.Categories) > 0) {
		return false
	}
	return r.Body == p.PageRequest(body.Offset, body.Categories).Body
}
func NativeBrowserResourceMatches(provider, board, metadata, resource string) bool {
	_, listing, e := NativeBrowserOptions(provider, board, metadata)
	if e != nil {
		return false
	}
	if resource == listing {
		return true
	}
	if provider == "accenture" {
		options, _, err := NativeBrowserOptions(provider, board, metadata)
		return err == nil && resource == options.Inventory.Endpoint
	}
	if provider == "brassring" {
		_, options, err := BrassRingBrowserOptions(board, metadata)
		if err != nil {
			return false
		}
		return resource == options.Inventory.Endpoint || resource == strings.TrimSuffix(options.Inventory.Endpoint, "MatchedJobs")+"ProcessSortAndShowMoreJobs" || BrassRingDetailResourceMatches(board, resource)
	}
	if provider == "darwinbox" {
		b, e := DarwinboxBoardFromURL(board)
		return e == nil && resource == b.JobsURL()
	}
	p, e := ByteDanceOptionsFromURL(board)
	return e == nil && (resource == p.Endpoint || p.PartitionCategories && resource == ByteDanceFilterURL)
}
func NativeBrowserRequestOptions(o BrowserReplayOptions, r Request) BrowserReplayOptions {
	// Callers must validate the fixed provider scope before this narrow clone.
	o.Inventory = Options{Endpoint: r.URL, Method: r.Method, Body: r.Body, Headers: r.Headers.Clone()}
	return o
}
