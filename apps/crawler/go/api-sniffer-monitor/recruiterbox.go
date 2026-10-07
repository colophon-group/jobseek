package apisniffer

import (
	"context"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type RecruiterboxOptions struct{ Tenant string }
type RecruiterboxPage struct {
	Total    int
	URLs     []string
	Complete bool
}

var recruiterboxTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var recruiterboxJobPath = regexp.MustCompile(`(?i)^/jobs/([a-z0-9]{3,64})$`)
var recruiterboxJobLink = regexp.MustCompile(`(?i)/jobs/[a-z0-9]{3,64}/?(?:[?#]|$)`)
var recruiterboxPositiveDigits = regexp.MustCompile(`^[0-9]+$`)
var recruiterboxTotal = regexp.MustCompile(`(?i)(?:\btotal_jobs|['"]total_jobs['"])\s*:\s*['"]?([0-9]{1,9})`)

func recruiterboxTenantName(value string) string {
	v := strings.ToLower(strings.TrimSpace(value))
	if !recruiterboxTenant.MatchString(v) {
		return ""
	}
	switch v {
	case "api", "app", "help", "static", "support", "www":
		return ""
	}
	return v
}
func recruiterboxBoardFromURL(source string) (RecruiterboxOptions, bool) {
	u, e := url.Parse(source)
	if e != nil || len(source) > 4096 || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || u.Opaque != "" {
		return RecruiterboxOptions{}, false
	}
	tenant := ""
	for _, suffix := range []string{".recruiterbox.com", ".hire.trakstar.com"} {
		if strings.HasSuffix(strings.ToLower(u.Hostname()), suffix) {
			tenant = recruiterboxTenantName(strings.TrimSuffix(strings.ToLower(u.Hostname()), suffix))
		}
	}
	if tenant == "" {
		return RecruiterboxOptions{}, false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(strings.Split(u.RawQuery, "&")) > 8 {
		return RecruiterboxOptions{}, false
	}
	for _, values := range q {
		if len(values) != 1 {
			return RecruiterboxOptions{}, false
		}
	}
	// Python matches the original escaped path, not URL-decoded job tokens.
	path := strings.TrimRight(u.EscapedPath(), "/")
	if path == "" || path == "/jobs" {
		for key, values := range q {
			if key != "limit" && key != "p" || !recruiterboxPositiveDigits.MatchString(values[0]) || strings.TrimLeft(values[0], "0") == "" {
				return RecruiterboxOptions{}, false
			}
		}
	} else {
		if !recruiterboxJobPath.MatchString(path) {
			return RecruiterboxOptions{}, false
		}
		for key, values := range q {
			if key != "source" || values[0] == "" {
				return RecruiterboxOptions{}, false
			}
		}
	}
	return RecruiterboxOptions{tenant}, true
}
func RecruiterboxOptionsFromMetadata(source, raw string) (RecruiterboxOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return RecruiterboxOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return RecruiterboxOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return RecruiterboxOptions{}, ErrOptions
	}
	if value, ok := m["tenant"].(string); ok {
		if tenant := recruiterboxTenantName(value); tenant != "" {
			return RecruiterboxOptions{tenant}, nil
		}
	}
	o, ok := recruiterboxBoardFromURL(source)
	if !ok {
		return RecruiterboxOptions{}, ErrOptions
	}
	return o, nil
}
func (o RecruiterboxOptions) ListingURL() string {
	return "https://" + o.Tenant + ".hire.trakstar.com/"
}
func (o RecruiterboxOptions) PageURL(page int) string {
	return o.ListingURL() + "?limit=100&p=" + strconv.Itoa(page)
}
func (o RecruiterboxOptions) ResourceMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil {
		return false
	}
	p, e := strconv.Atoi(u.Query().Get("p"))
	return e == nil && p >= 1 && p <= 500 && source == o.PageURL(p)
}
func RecruiterboxInactive(body string) bool {
	body = strings.ToLower(body)
	return strings.Contains(body, "recruiterbox.com/inactive-ats") && strings.Contains(body, "inactive account") && strings.Contains(body, "no longer using trakstar hire")
}
func ParseRecruiterboxPage(body string, o RecruiterboxOptions, page int, join JoinURL) (RecruiterboxPage, error) {
	p := RecruiterboxPage{URLs: []string{}}
	runes := []rune(body)
	overCap := len(runes) > 2000000
	if overCap {
		body = string(runes[:2000000])
	}
	match := recruiterboxTotal.FindStringSubmatch(body)
	if match == nil || join == nil || page < 1 {
		return p, ErrInventory
	}
	p.Total, _ = strconv.Atoi(match[1])
	hrefs, e := dom.ListingHrefs(body, "")
	if e != nil {
		return p, e
	}
	seen := map[string]bool{}
	for _, href := range hrefs {
		source, e := join(o.ListingURL(), href)
		if e != nil || !recruiterboxJobLink.MatchString(source) {
			continue
		}
		board, ok := recruiterboxBoardFromURL(source)
		if !ok || board != o {
			continue
		}
		u, _ := url.Parse(source)
		job := recruiterboxJobPath.FindStringSubmatch(strings.TrimRight(u.EscapedPath(), "/"))
		if job != nil {
			seen[o.ListingURL()+"jobs/"+strings.ToLower(job[1])+"/"] = true
		}
	}
	for source := range seen {
		p.URLs = append(p.URLs, source)
	}
	sort.Strings(p.URLs)
	expected := min(100, max(p.Total-(page-1)*100, 0))
	if expected > 0 && len(p.URLs) == 0 {
		return p, ErrInventory
	}
	p.Complete = len(p.URLs) == expected && !overCap
	return p, nil
}

// A missing later page is an incomplete inventory; other fetch failures must
// discard the entire provisional result. The fetch callback distinguishes both.
func DiscoverRecruiterbox(ctx context.Context, o RecruiterboxOptions, fetch func(context.Context, string) (string, bool, error), join JoinURL) ([]string, bool, error) {
	body, missing, e := fetch(ctx, o.PageURL(1))
	if e != nil {
		return nil, false, e
	}
	if missing {
		return nil, false, ErrInventory
	}
	p, e := ParseRecruiterboxPage(body, o, 1, join)
	if e != nil {
		return nil, false, e
	}
	total := p.Total
	truncated := !p.Complete || total > 50000
	seen := map[string]bool{}
	for page := 1; page <= max(1, min((total+99)/100, 500)); page++ {
		if page > 1 {
			body, missing, e = fetch(ctx, o.PageURL(page))
			if e != nil {
				return nil, false, e
			}
			if missing {
				truncated = true
				break
			}
			p, e = ParseRecruiterboxPage(body, o, page, join)
			if e != nil {
				return nil, false, e
			}
		}
		if p.Total != total || !p.Complete {
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
	truncated = truncated || len(urls) != min(total, 50000)
	if truncated && len(urls) == 0 {
		return nil, false, ErrInventory
	}
	return urls, truncated, nil
}
