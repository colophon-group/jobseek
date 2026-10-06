package apisniffer

import (
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"html"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type JobviteOptions struct{ Tenant, Endpoint string }
type JobviteSearch struct {
	Category string
	Page     int
}
type JobvitePage struct {
	Jobs, Listings, Searches []string
	Start, End, Total        int
	HasRange                 bool
}

var jobviteTenant = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)
var jobviteID = regexp.MustCompile(`^[A-Za-z0-9_-]{6,64}$`)
var jobviteMarker = regexp.MustCompile(`(?i)\bcareersiteName\s*:\s*(['"])([a-z0-9][a-z0-9-]{0,62})(['"])`)
var jobviteJobAnchor = regexp.MustCompile(`(?i)^https://jobs\.jobvite\.com/[a-z0-9][a-z0-9-]{0,62}/job/[A-Za-z0-9_-]{6,64}(?:[/?#]|$)`)
var jobviteListingAnchor = regexp.MustCompile(`(?i)^https://jobs\.jobvite\.com/(?:[a-z0-9][a-z0-9-]{0,62}(?:/jobs(?:/positions)?)?|careers/[a-z0-9][a-z0-9-]{0,62}(?:/jobs)?)(?:[?#]|$)`)
var jobviteSearchAnchor = regexp.MustCompile(`(?i)^https://jobs\.jobvite\.com/[a-z0-9][a-z0-9-]{0,62}/search/?\?[^#\s]+$`)
var jobviteRange = regexp.MustCompile(`(?i)<div\s+class=["']jv-pagination-text["']>\s*(\d+)\s*-\s*(\d+)\s+of\s+(\d+)\s*</div>`)

func jobviteTenantName(s string) string {
	s = strings.ToLower(s)
	if !jobviteTenant.MatchString(s) || s == "api" || s == "careers" || s == "help" || s == "support" || s == "www" {
		return ""
	}
	return s
}
func jobviteURL(s string) (*url.URL, bool) {
	u, e := url.Parse(html.UnescapeString(s))
	return u, e == nil && strings.EqualFold(u.Scheme, "https") && strings.TrimSuffix(strings.ToLower(u.Hostname()), ".") == "jobs.jobvite.com" && u.User == nil && u.Opaque == "" && (u.Port() == "" || u.Port() == "443") && u.RawPath == ""
}
func jobviteParts(u *url.URL) []string {
	p := []string{}
	for _, v := range strings.Split(u.Path, "/") {
		if v != "" {
			p = append(p, v)
		}
	}
	return p
}
func JobviteBoardFromURL(s string) (JobviteOptions, bool) {
	u, ok := jobviteURL(s)
	if !ok || u.Fragment != "" {
		return JobviteOptions{}, false
	}
	p := jobviteParts(u)
	tenant, path := "", ""
	switch {
	case len(p) == 1 && u.RawQuery == "":
		tenant = jobviteTenantName(p[0])
		path = "/" + tenant
	case len(p) == 2 && strings.EqualFold(p[0], "careers") && u.RawQuery == "":
		tenant = jobviteTenantName(p[1])
		path = "/careers/" + tenant
	case len(p) == 2 && strings.EqualFold(p[1], "jobs"):
		tenant = jobviteTenantName(p[0])
		path = "/" + tenant + "/jobs"
	case len(p) == 3 && strings.EqualFold(p[1], "jobs") && strings.EqualFold(p[2], "positions"):
		tenant = jobviteTenantName(p[0])
		path = "/" + tenant + "/jobs/positions"
	case len(p) == 3 && strings.EqualFold(p[0], "careers") && strings.EqualFold(p[2], "jobs"):
		tenant = jobviteTenantName(p[1])
		path = "/careers/" + tenant + "/jobs"
	case len(p) == 3 && strings.EqualFold(p[1], "job") && jobviteID.MatchString(p[2]):
		tenant = jobviteTenantName(p[0])
		path = "/" + tenant
	}
	if tenant == "" {
		return JobviteOptions{}, false
	}
	return JobviteOptions{tenant, "https://jobs.jobvite.com" + path}, true
}
func JobviteOptionsFromMetadata(source, raw string) (JobviteOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return JobviteOptions{}, ErrOptions
	}
	for _, k := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return JobviteOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return JobviteOptions{}, ErrOptions
	}
	direct, hasDirect := JobviteBoardFromURL(source)
	var configured JobviteOptions
	hasConfigured := false
	if v, exists := m["tenant"]; exists {
		s, ok := v.(string)
		if !ok || jobviteTenantName(s) == "" {
			return JobviteOptions{}, ErrOptions
		}
		configured = JobviteOptions{jobviteTenantName(s), "https://jobs.jobvite.com/" + jobviteTenantName(s)}
		hasConfigured = true
	}
	if v, exists := m["listing_url"]; exists {
		s, ok := v.(string)
		if !ok {
			return JobviteOptions{}, ErrOptions
		}
		o, ok := JobviteBoardFromURL(s)
		if !ok || hasConfigured && o.Tenant != configured.Tenant {
			return JobviteOptions{}, ErrOptions
		}
		configured = o
		hasConfigured = true
	}
	if hasConfigured {
		if hasDirect && direct.Tenant != configured.Tenant {
			return JobviteOptions{}, ErrOptions
		}
		return configured, nil
	}
	if !hasDirect {
		return JobviteOptions{}, ErrOptions
	}
	return direct, nil
}
func (o JobviteOptions) SearchURL(s JobviteSearch) string {
	return "https://jobs.jobvite.com/" + o.Tenant + "/search?c=" + url.QueryEscape(s.Category) + "&p=" + strconv.Itoa(s.Page)
}
func (o JobviteOptions) SearchFromURL(source string) (JobviteSearch, bool) {
	u, ok := jobviteURL(source)
	if !ok || u.Fragment != "" {
		return JobviteSearch{}, false
	}
	p := jobviteParts(u)
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(p) != 2 || !strings.EqualFold(p[0], o.Tenant) || !strings.EqualFold(p[1], "search") || len(q) != 2 || len(q["c"]) != 1 || len(q["p"]) != 1 {
		return JobviteSearch{}, false
	}
	category := strings.TrimSpace(q.Get("c"))
	if category == "" || len([]rune(category)) > 256 {
		return JobviteSearch{}, false
	}
	for _, r := range category {
		if r < 32 || r == 127 {
			return JobviteSearch{}, false
		}
	}
	v := q.Get("p")
	if v == "" {
		return JobviteSearch{}, false
	}
	for _, r := range v {
		if r < '0' || r > '9' {
			return JobviteSearch{}, false
		}
	}
	page, e := strconv.Atoi(v)
	if e != nil || page >= 5000 {
		return JobviteSearch{}, false
	}
	return JobviteSearch{category, page}, true
}
func (o JobviteOptions) ResourceMatches(s string) bool {
	if s == o.Endpoint {
		return true
	}
	if v, ok := JobviteBoardFromURL(s); ok && v.Tenant == o.Tenant && v.Endpoint == s {
		return true
	}
	v, ok := o.SearchFromURL(s)
	return ok && o.SearchURL(v) == s
}
func (o JobviteOptions) JobURL(s string) string {
	u, ok := jobviteURL(s)
	if !ok {
		return ""
	}
	p := jobviteParts(u)
	if len(p) != 3 || !strings.EqualFold(p[0], o.Tenant) || !strings.EqualFold(p[1], "job") || !jobviteID.MatchString(p[2]) {
		return ""
	}
	return "https://jobs.jobvite.com/" + o.Tenant + "/job/" + p[2]
}
func JobviteListing(body string, o JobviteOptions, source string) (JobvitePage, error) {
	out := JobvitePage{}
	marker := jobviteMarker.FindStringSubmatch(body)
	if !strings.Contains(body, `ng-app="jv.careersite.desktop.app"`) || len(marker) != 4 || marker[1] != marker[3] || jobviteTenantName(marker[2]) != o.Tenant {
		return out, ErrInventory
	}
	classification, e := dom.ClassifyDocument(body, dom.Object{}, source)
	if e != nil || classification["classification"] == "challenge" {
		return out, ErrInventory
	}
	hrefs, e := dom.ListingHrefs(body, "")
	if e != nil {
		return out, e
	}
	base, e := url.Parse(source)
	if e != nil {
		return out, ErrOptions
	}
	jobs, listings, searches := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, href := range hrefs {
		u, e := url.Parse(href)
		if e != nil {
			continue
		}
		raw := base.ResolveReference(u).String()
		// Match the original first-party anchor filters before canonicalizing.
		if v := o.JobURL(raw); v != "" && jobviteJobAnchor.MatchString(raw) {
			jobs[v] = true
			continue
		}
		if v, ok := JobviteBoardFromURL(raw); ok && v.Tenant == o.Tenant && v.Endpoint != o.Endpoint && jobviteListingAnchor.MatchString(raw) {
			listings[v.Endpoint] = true
		}
		if v, ok := o.SearchFromURL(raw); ok && jobviteSearchAnchor.MatchString(raw) {
			searches[o.SearchURL(v)] = true
		}
	}
	if len(jobs) > 50000 || len(searches) > 128 {
		return out, ErrInventory
	}
	ordered := func(m map[string]bool) []string {
		v := []string{}
		for s := range m {
			v = append(v, s)
		}
		sort.Strings(v)
		return v
	}
	out.Jobs, out.Listings, out.Searches = ordered(jobs), ordered(listings), ordered(searches)
	if m := jobviteRange.FindStringSubmatch(body); len(m) == 4 {
		out.HasRange = true
		var err error
		out.Start, err = strconv.Atoi(m[1])
		if err != nil {
			return JobvitePage{}, ErrInventory
		}
		out.End, err = strconv.Atoi(m[2])
		if err != nil {
			return JobvitePage{}, ErrInventory
		}
		out.Total, err = strconv.Atoi(m[3])
		if err != nil {
			return JobvitePage{}, ErrInventory
		}
	}
	return out, nil
}
func JobviteInvalidRedirect(s string) bool {
	u, e := url.Parse(s)
	return e == nil && strings.EqualFold(u.Scheme, "https") && strings.TrimSuffix(strings.ToLower(u.Hostname()), ".") == "www.jobvite.com" && strings.TrimRight(u.Path, "/") == "/support/job-seeker-support" && u.RawQuery == "invalid=1"
}
