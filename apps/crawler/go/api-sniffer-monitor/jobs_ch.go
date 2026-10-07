package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type JobCloudOptions struct{ CompanyID, DocumentCompanyID, Locale, Portal string }
type JobCloudPage struct {
	Documents    []any
	Pages, Total int
}

var jobCloudUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var jobCloudDigits = regexp.MustCompile(`^[0-9]{1,20}$`)
var jobCloudCompany = regexp.MustCompile(`(?i)^/(de|fr|en)/(?:firmen|entreprises|companies)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]{1,20})(?:[-/]|$)`)
var jobupCompany = regexp.MustCompile(`(?i)^/(fr|en)/(?:societes|enterprises)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]{1,20})(?:[-/]|$)`)

func jobCloudCompanyID(v any) string {
	var s string
	switch n := v.(type) {
	case string:
		s = strings.TrimSpace(n)
	case json.Number:
		if !strings.ContainsAny(n.String(), ".eE") {
			s = n.String()
		}
	}
	if jobCloudDigits.MatchString(s) {
		return s
	}
	if jobCloudUUID.MatchString(s) {
		return strings.ToLower(s)
	}
	return ""
}
func JobCloudOptionsFromMetadata(source, raw string) (JobCloudOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return JobCloudOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return JobCloudOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return JobCloudOptions{}, ErrOptions
	}
	o := JobCloudOptions{Locale: "de", Portal: "jobs_ch"}
	u, _ := url.Parse(source)
	if u.Scheme == "https" && u.User == nil && (u.Port() == "" || u.Port() == "443") {
		var match []string
		switch strings.ToLower(u.Hostname()) {
		case "jobs.ch", "www.jobs.ch":
			match = jobCloudCompany.FindStringSubmatch(u.EscapedPath())
		case "jobup.ch", "www.jobup.ch":
			match = jobupCompany.FindStringSubmatch(u.EscapedPath())
			if match != nil {
				o.Portal = "jobup"
			}
		}
		if match != nil {
			o.Locale = strings.ToLower(match[1])
			o.CompanyID = jobCloudCompanyID(match[2])
		}
	}
	if detailTruthy(m["company_id"]) {
		o.CompanyID = jobCloudCompanyID(m["company_id"])
	}
	if o.CompanyID == "" {
		return o, ErrOptions
	}
	o.DocumentCompanyID = o.CompanyID
	if v, exists := m["document_company_id"]; exists {
		o.DocumentCompanyID = jobCloudCompanyID(v)
		if o.DocumentCompanyID == "" {
			return o, ErrOptions
		}
	}
	for key, target := range map[string]*string{"locale": &o.Locale, "portal": &o.Portal} {
		if detailTruthy(m[key]) {
			v, ok := m[key].(string)
			if !ok {
				return o, ErrOptions
			}
			*target = strings.ToLower(v)
		}
	}
	if o.DetailPath() == "" || o.DocumentCompanyID != o.CompanyID && (jobCloudDigits.MatchString(o.CompanyID) || !jobCloudDigits.MatchString(o.DocumentCompanyID)) {
		return o, ErrOptions
	}
	return o, nil
}
func (o JobCloudOptions) DetailPath() string {
	if o.Portal == "jobs_ch" {
		return map[string]string{"de": "stellenangebote", "fr": "offres-emplois", "en": "vacancies"}[o.Locale]
	}
	if o.Portal == "jobup" {
		return map[string]string{"fr": "emplois", "en": "jobs"}[o.Locale]
	}
	return ""
}
func (o JobCloudOptions) portalHost() string {
	if o.Portal == "jobup" {
		return "jobup.ch"
	}
	return "jobs.ch"
}
func (o JobCloudOptions) SearchURL(page int) string {
	q := url.Values{"companyIds": {o.CompanyID}, "page": {strconv.Itoa(page)}, "publishedOn": {"SEARCH", "SEARCH_COMPANY_PROFILE"}, "rows": {"100"}}
	return "https://job-search-api." + o.portalHost() + "/search?" + q.Encode()
}
func (o JobCloudOptions) ResourceMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil {
		return false
	}
	p, e := strconv.Atoi(u.Query().Get("page"))
	return e == nil && p >= 1 && p <= 500 && source == o.SearchURL(p)
}
func jobCloudInteger(v any) (int, bool) {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(n.String(), ".eE") {
		return 0, false
	}
	i, e := strconv.Atoi(n.String())
	return i, e == nil && i >= 0
}
func jobCloudNumberEquals(v any, want int) bool {
	if b, ok := v.(bool); ok {
		return want == 0 && !b || want == 1 && b
	}
	n, ok := v.(json.Number)
	if !ok {
		return false
	}
	f, e := n.Float64()
	return e == nil && f == float64(want)
}
func ParseJobCloudPage(d *Document, page int) (JobCloudPage, error) {
	p := JobCloudPage{}
	if d == nil {
		return p, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return p, ErrInventory
	}
	p.Documents, ok = m["documents"].([]any)
	if !ok {
		return p, ErrInventory
	}
	p.Pages, ok = jobCloudInteger(m["numPages"])
	if !ok || p.Pages > 500 {
		return p, ErrInventory
	}
	p.Total, ok = jobCloudInteger(m["totalHits"])
	if !ok {
		return p, ErrInventory
	}
	current, ok := jobCloudInteger(m["currentPage"])
	if !ok || current != page || !jobCloudNumberEquals(m["rows"], 100) || !jobCloudNumberEquals(m["start"], (page-1)*100) {
		return p, ErrInventory
	}
	return p, nil
}
func DiscoverJobCloud(ctx context.Context, o JobCloudOptions, fetch func(context.Context, string) (*Document, error)) ([]string, error) {
	d, e := fetch(ctx, o.SearchURL(1))
	if e != nil {
		return nil, e
	}
	first, e := ParseJobCloudPage(d, 1)
	if e != nil {
		return nil, e
	}
	if first.Pages != first.Total/100+boolInt(first.Total%100 != 0) {
		return nil, ErrInventory
	}
	pages := []JobCloudPage{first}
	for page := 2; page <= first.Pages; page++ {
		d, e := fetch(ctx, o.SearchURL(page))
		if e != nil {
			return nil, e
		}
		p, e := ParseJobCloudPage(d, page)
		if e != nil || p.Pages != first.Pages || p.Total != first.Total {
			return nil, ErrInventory
		}
		pages = append(pages, p)
	}
	seen := map[string]bool{}
	for index, p := range pages {
		if len(p.Documents) != min(100, max(0, first.Total-index*100)) {
			return nil, ErrInventory
		}
		for _, raw := range p.Documents {
			row, ok := raw.(map[string]any)
			if !ok {
				return nil, ErrInventory
			}
			company, ok := row["company"].(map[string]any)
			if !ok || jobCloudCompanyID(company["id"]) != o.DocumentCompanyID {
				return nil, ErrInventory
			}
			id, ok := row["id"].(string)
			if !ok || !jobCloudUUID.MatchString(id) {
				return nil, ErrInventory
			}
			seen["https://www."+o.portalHost()+"/"+o.Locale+"/"+o.DetailPath()+"/detail/"+strings.ToLower(id)+"/"] = true
		}
	}
	if len(seen) != first.Total {
		return nil, ErrInventory
	}
	urls := []string{}
	for source := range seen {
		urls = append(urls, source)
	}
	sort.Strings(urls)
	return urls, nil
}
func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
