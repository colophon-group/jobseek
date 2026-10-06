package dom

import (
	"errors"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"net/url"
	"strconv"
	"strings"
)

// Static URL pagination retains the existing 10,000-page limit. Browser
// actions, partitions and advertised totals need their own inventory proof.
type ListingPagination struct {
	Param, Template                      string
	Start, Increment, MaxPages, Attempts int
	Transient403                         bool
}

func listingPagination(raw any, endpoint string) (*ListingPagination, error) {
	if !truth(raw) {
		return nil, nil
	}
	m, err := object(raw)
	if err != nil {
		return nil, err
	}
	allowed := map[string]bool{}
	for _, key := range []string{"param_name", "url_template", "start", "start_value", "increment", "max_pages", "browser", "transient_403", "transport_attempts"} {
		allowed[key] = true
	}
	for key := range m {
		if !allowed[key] {
			return nil, errors.New("unsupported DOM pagination contract")
		}
	}
	if v := m["browser"]; v != nil {
		if _, ok := v.(bool); !ok {
			return nil, errors.New("invalid pagination browser flag")
		}
	}
	if truth(m["browser"]) {
		return nil, errors.New("unsupported browser pagination")
	}
	p := &ListingPagination{Start: 1, Increment: 1, MaxPages: 10000, Attempts: 3}
	if m["start"] == nil {
		m["start"] = m["start_value"]
	}
	for key, dest := range map[string]*int{"start": &p.Start, "increment": &p.Increment, "max_pages": &p.MaxPages, "transport_attempts": &p.Attempts} {
		if m[key] != nil {
			if _, ok := m[key].(bool); ok {
				return nil, errors.New("invalid pagination integer")
			}
			*dest, err = number(m[key], *dest)
			if err != nil {
				return nil, err
			}
		}
	}
	if p.Start < 0 || p.Start > 1000000 || p.Increment < 1 || p.Increment > 1000000 || p.MaxPages < 1 || p.Attempts < 1 || p.Attempts > 5 {
		return nil, errors.New("invalid DOM pagination budget")
	}
	p.MaxPages = min(p.MaxPages, 10000)
	if v := m["transient_403"]; v != nil {
		var ok bool
		p.Transient403, ok = v.(bool)
		if !ok {
			return nil, errors.New("invalid pagination transient status")
		}
	}
	if v := m["param_name"]; v != nil {
		var ok bool
		p.Param, ok = v.(string)
		if !ok || len(p.Param) > 128 || strings.ContainsAny(p.Param, "\x00\r\n") {
			return nil, errors.New("invalid pagination parameter")
		}
	}
	if v := m["url_template"]; v != nil {
		var ok bool
		p.Template, ok = v.(string)
		if !ok || strings.Count(p.Template, "{page}") != 1 || strings.ContainsAny(strings.ReplaceAll(p.Template, "{page}", ""), "{}") {
			return nil, errors.New("invalid pagination template")
		}
	}
	if p.Template == "" && p.Param == "" {
		return nil, errors.New("pagination has no URL contract")
	}
	for _, page := range []int{2, p.MaxPages} {
		if page >= 2 && !jsonld.ValidPublicEndpoint(p.URL(endpoint, page)) {
			return nil, errors.New("invalid pagination endpoint")
		}
	}
	return p, nil
}

func (p *ListingPagination) URL(endpoint string, page int) string {
	if p == nil || page < 2 || page > p.MaxPages {
		return ""
	}
	value := strconv.Itoa(p.Start + (page-1)*p.Increment)
	if p.Template != "" {
		return strings.ReplaceAll(p.Template, "{page}", value)
	}
	u, err := url.Parse(endpoint)
	if err != nil {
		return ""
	}
	// Python parse_qs/urlencode retains each key's first appearance. Fetch
	// identities must preserve that ordering and repeated non-page values.
	keys := []string{}
	values := map[string][]string{}
	for _, pair := range strings.Split(u.RawQuery, "&") {
		if pair == "" {
			continue
		}
		key, raw, _ := strings.Cut(pair, "=")
		key, err = url.QueryUnescape(key)
		if err != nil {
			return ""
		}
		raw, err = url.QueryUnescape(raw)
		if err != nil {
			return ""
		}
		if _, ok := values[key]; !ok {
			keys = append(keys, key)
		}
		values[key] = append(values[key], raw)
	}
	if _, ok := values[p.Param]; !ok {
		keys = append(keys, p.Param)
	}
	values[p.Param] = []string{value}
	encoded := []string{}
	for _, key := range keys {
		for _, val := range values[key] {
			encoded = append(encoded, url.QueryEscape(key)+"="+url.QueryEscape(val))
		}
	}
	u.RawQuery = strings.Join(encoded, "&")
	return u.String()
}

func (c ListingConfig) ResourceMatches(endpoint, resource string) bool {
	if resource == endpoint {
		return true
	}
	if c.Pagination == nil {
		return false
	}
	for page := 2; page <= c.Pagination.MaxPages; page++ {
		if resource == c.Pagination.URL(endpoint, page) {
			return true
		}
	}
	return false
}
