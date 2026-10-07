package apisniffer

import (
	"context"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type HRMOSOptions struct{ Tenant string }
type HRMOSPage struct {
	URLs                                 []string
	Total, Displayed, Current, LinkedMax int
	Empty, AtCap                         bool
	CurrentPresent                       bool
}

var hrmosTenant = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,62}$`)
var hrmosJob = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var hrmosListing = regexp.MustCompile(`(?i)\bid=["']jsi-joblist["']`)
var hrmosEmpty = regexp.MustCompile(`(?i)class=["'][^"']*\bsg-unavailable-notifier\b[^"']*["']`)
var hrmosCount = regexp.MustCompile(`全\s*([\d,]+)\s*件中\s*([\d,]+)\s*件`)
var hrmosCurrent = regexp.MustCompile(`(?i)class=["'][^"']*\bcurrent\b[^"']*["'][^>]*>\s*(\d+)\s*<`)
var hrmosPageParam = regexp.MustCompile(`(?i)[?&](?:amp;)?page=(\d+)`)

func HRMOSOptionsFromMetadata(source, raw string) (HRMOSOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return HRMOSOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return HRMOSOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return HRMOSOptions{}, ErrOptions
	}
	tenant, _ := m["tenant"].(string)
	tenant = strings.ToLower(strings.TrimSpace(tenant))
	if !hrmosTenant.MatchString(tenant) {
		u, _ := url.Parse(source)
		p := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
		if u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "hrmos.co") || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || len(p) < 3 || len(p) > 4 || !strings.EqualFold(p[0], "pages") || !strings.EqualFold(p[2], "jobs") || len(p) == 4 && !hrmosJob.MatchString(p[3]) {
			return HRMOSOptions{}, ErrOptions
		}
		tenant = strings.ToLower(strings.TrimSpace(p[1]))
	}
	if !hrmosTenant.MatchString(tenant) {
		return HRMOSOptions{}, ErrOptions
	}
	return HRMOSOptions{tenant}, nil
}
func (o HRMOSOptions) ListingURL(page int) string {
	s := "https://hrmos.co/pages/" + o.Tenant + "/jobs"
	if page > 1 {
		s += "?page=" + strconv.Itoa(page)
	}
	return s
}
func (o HRMOSOptions) ResourceMatches(source string) bool {
	if source == o.ListingURL(1) {
		return true
	}
	u, e := url.Parse(source)
	if e != nil {
		return false
	}
	p, e := strconv.Atoi(u.Query().Get("page"))
	return e == nil && p >= 2 && p <= 500 && source == o.ListingURL(p)
}
func ParseHRMOSPage(body string, o HRMOSOptions, join JoinURL) (HRMOSPage, error) {
	p := HRMOSPage{URLs: []string{}, LinkedMax: 1, AtCap: len([]rune(body)) >= 2000000}
	listing, empty := hrmosListing.MatchString(body), hrmosEmpty.MatchString(body)
	if !listing && !empty {
		return p, ErrInventory
	}
	if empty && !listing {
		p.Empty = true
		return p, nil
	}
	count := hrmosCount.FindStringSubmatch(body)
	if count == nil {
		return p, ErrInventory
	}
	var e error
	p.Total, e = strconv.Atoi(strings.ReplaceAll(count[1], ",", ""))
	if e != nil {
		return p, ErrInventory
	}
	p.Displayed, e = strconv.Atoi(strings.ReplaceAll(count[2], ",", ""))
	if e != nil {
		return p, ErrInventory
	}
	if current := hrmosCurrent.FindStringSubmatch(body); current != nil {
		p.CurrentPresent = true
		p.Current, e = strconv.Atoi(current[1])
		if e != nil {
			return p, ErrInventory
		}
	}
	for _, match := range hrmosPageParam.FindAllStringSubmatchIndex(body, -1) {
		// Preserve Python's non-consuming delimiter lookahead, including
		// adjacent pagination parameters in the same query string.
		if match[1] < len(body) && !strings.ContainsRune(`&#"'`, rune(body[match[1]])) && !unicode.IsSpace([]rune(body[match[1]:])[0]) {
			continue
		}
		n, e := strconv.Atoi(body[match[2]:match[3]])
		if e != nil {
			return p, ErrInventory
		}
		p.LinkedMax = max(p.LinkedMax, n)
	}
	hrefs, e := dom.ListingHrefs(body, "")
	if e != nil || join == nil {
		return p, ErrInventory
	}
	seen := map[string]bool{}
	matcher := regexp.MustCompile(`(?i)^https://hrmos\.co/pages/` + regexp.QuoteMeta(o.Tenant) + `/jobs/[A-Za-z0-9_-]{1,64}(?:[/?#]|$)`)
	for _, href := range hrefs {
		absolute, e := join(o.ListingURL(1), href)
		if e != nil || !matcher.MatchString(absolute) {
			continue
		}
		u, e := url.Parse(absolute)
		if e != nil || u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "hrmos.co") || u.User != nil || u.Port() != "" && u.Port() != "443" {
			continue
		}
		parts := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
		if len(parts) != 4 || !strings.EqualFold(parts[0], "pages") || !strings.EqualFold(parts[1], o.Tenant) || !strings.EqualFold(parts[2], "jobs") || !hrmosJob.MatchString(parts[3]) {
			continue
		}
		// Python's matcher runs before canonicalization and admits no suffix
		// path after the ID, but permits query/fragment/trailing slash.
		source := o.ListingURL(1) + "/" + parts[3]
		seen[source] = true
	}
	for source := range seen {
		p.URLs = append(p.URLs, source)
	}
	sort.Strings(p.URLs)
	return p, nil
}

func DiscoverHRMOS(ctx context.Context, o HRMOSOptions, fetch func(context.Context, string) (string, error), join JoinURL) ([]string, bool, error) {
	first, e := fetch(ctx, o.ListingURL(1))
	if e != nil {
		return nil, false, e
	}
	p, e := ParseHRMOSPage(first, o, join)
	if e != nil {
		return nil, false, e
	}
	if p.Empty {
		return []string{}, false, nil
	}
	if p.CurrentPresent && p.Current != 1 || p.Total > 0 && p.Displayed == 0 {
		return nil, false, ErrInventory
	}
	pages := max(1, p.LinkedMax, (max(p.Total, 1)-1)/max(p.Displayed, 1)+1)
	expected := p.Total
	truncated := pages > 500 || expected > 50000
	seen := map[string]bool{}
	for page := 1; page <= min(pages, 500); page++ {
		if page > 1 {
			if len(seen) >= 50000 {
				truncated = true
				break
			}
			body, e := fetch(ctx, o.ListingURL(page))
			if e != nil {
				return nil, false, e
			}
			p, e = ParseHRMOSPage(body, o, join)
			if e != nil {
				return nil, false, e
			}
		}
		if p.Empty || p.CurrentPresent && p.Current != page || page > 1 && len(p.URLs) == 0 {
			return nil, false, ErrInventory
		}
		if p.Total != expected || p.Displayed != len(p.URLs) || p.AtCap {
			truncated = true
		}
		for _, source := range p.URLs {
			if seen[source] {
				truncated = true
			}
			seen[source] = true
		}
	}
	urls := []string{}
	for source := range seen {
		urls = append(urls, source)
	}
	sort.Strings(urls)
	return urls, truncated || len(urls) != expected, nil
}
