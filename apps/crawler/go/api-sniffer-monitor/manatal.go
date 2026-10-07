package apisniffer

import (
	"context"
	"encoding/json"
	"math/big"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type ManatalOptions struct{ Slug string }

var manatalSlug = regexp.MustCompile(`^[a-z0-9_-]{1,256}$`)

func ManatalOptionsFromMetadata(source, raw string) (ManatalOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return ManatalOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return ManatalOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return ManatalOptions{}, ErrOptions
	}
	slug, _ := m["slug"].(string)
	if !detailTruthy(m["slug"]) {
		u, _ := url.Parse(source)
		if u.Host != "www.careers-page.com" && u.Host != "careers-page.com" {
			return ManatalOptions{}, ErrOptions
		}
		slug = strings.ToLower(strings.Split(strings.TrimLeft(u.Path, "/"), "/")[0])
		switch slug {
		case "api", "assets", "job", "jobs", "login", "static", "www":
			return ManatalOptions{}, ErrOptions
		}
	}
	if !manatalSlug.MatchString(slug) {
		return ManatalOptions{}, ErrOptions
	}
	return ManatalOptions{slug}, nil
}

func (o ManatalOptions) ListingURL(page int) string {
	q := url.Values{"page_size": {"50"}, "page": {strconv.Itoa(page)}, "ordering": {"-is_pinned_in_career_page,-last_published_at"}}
	return "https://www.careers-page.com/api/v1.0/c/" + o.Slug + "/jobs/?" + q.Encode()
}

func (o ManatalOptions) ResourceMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil {
		return false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q["page"]) != 1 {
		return false
	}
	p, e := strconv.Atoi(q.Get("page"))
	return e == nil && p >= 1 && p <= 10000 && source == o.ListingURL(p)
}

// The next value is a continuation signal. Python computes the next page
// itself; it does not fetch the URL supplied by the response.
func ManatalPage(d *Document) (int64, []any, bool, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return 0, nil, false, ErrInventory
	}
	n, ok := m["count"].(json.Number)
	if !ok || strings.ContainsAny(n.String(), ".eE") {
		return 0, nil, false, ErrInventory
	}
	count, e := n.Int64()
	rows, ok := m["results"].([]any)
	if e != nil || count < 0 || !ok {
		return 0, nil, false, ErrInventory
	}
	return count, rows, detailTruthy(m["next"]), nil
}

func (d *Document) ManatalJobFields(raw any, o ManatalOptions) (map[string]any, error) {
	m, ok := raw.(map[string]any)
	if !ok || !detailTruthy(m["hash"]) {
		return nil, nil
	}
	hash, e := d.String(m["hash"])
	if e != nil {
		return nil, e
	}
	// The writer requires a valid source URL; do not replace a malformed
	// upstream identity with another identity.
	source := "https://www.careers-page.com/" + o.Slug + "/job/" + hash
	if !validURL(source) {
		return nil, ErrInventory
	}
	var locations []string
	if display, ok := m["location_display"].(string); ok && strings.TrimSpace(display) != "" {
		locations = []string{strings.TrimSpace(display)}
	} else {
		parts := []string{}
		for _, key := range []string{"city", "state", "country"} {
			if !detailTruthy(m[key]) {
				continue
			}
			part, e := d.String(m[key])
			if e != nil {
				return nil, e
			}
			if part = strings.TrimSpace(part); part != "" {
				parts = append(parts, part)
			}
		}
		if len(parts) > 0 {
			locations = []string{strings.Join(parts, ", ")}
		}
	}
	var metadata map[string]any
	if m["id"] != nil {
		metadata = map[string]any{"id": m["id"]}
	}
	return map[string]any{"url": source, "title": m["position_name"], "description": m["description"], "locations": locations, "metadata": metadata}, nil
}

func DiscoverManatal(ctx context.Context, o ManatalOptions, fetch func(context.Context, string) (*Document, error)) ([]map[string]any, bool, error) {
	jobs := []map[string]any{}
	seen := map[string]bool{}
	expected := int64(-1)
	for page := 1; page <= 10000; page++ {
		d, e := fetch(ctx, o.ListingURL(page))
		if e != nil {
			return nil, false, e
		}
		if d == nil {
			return nil, false, ErrInventory
		}
		total, rows, more, e := ManatalPage(d)
		if e != nil {
			return nil, false, e
		}
		if expected < 0 {
			expected = total
		}
		if total != expected {
			return nil, false, ErrInventory
		}
		before := len(jobs)
		for _, row := range rows {
			fields, e := d.ManatalJobFields(row, o)
			if e != nil {
				return nil, false, e
			}
			if fields == nil {
				continue
			}
			// Python deduplicates the upstream hash before constructing URLs.
			// Numeric42 and string"42" are distinct identities even though
			// their URL strings coincide; numeric1 and booleantrue are equal.
			var identity string
			switch hash := row.(map[string]any)["hash"].(type) {
			case string:
				identity = "string:" + hash
			case json.Number:
				n, ok := new(big.Rat).SetString(hash.String())
				if !ok {
					return nil, false, ErrInventory
				}
				identity = "number:" + n.RatString()
			case bool:
				identity = "number:1"
			default:
				return nil, false, ErrInventory
			}
			if seen[identity] {
				continue
			}
			seen[identity] = true
			jobs = append(jobs, fields)
			if len(jobs) >= 50000 {
				break
			}
		}
		if int64(len(jobs)) > expected {
			return nil, false, ErrInventory
		}
		if !more || len(jobs) >= 50000 {
			return jobs, int64(len(jobs)) < expected || len(jobs) >= 50000, nil
		}
		if len(jobs) == before {
			return nil, false, ErrInventory
		}
	}
	return nil, false, ErrInventory
}
