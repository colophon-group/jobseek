package apisniffer

import (
	"context"
	_ "embed"
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type LinkedInOptions struct {
	CompanyIDs            []string
	CompanySlug, Keywords string
	NumericURLs           bool
	ExcludedCountries     map[string]bool
}
type LinkedInListingJob struct {
	ID, URL, Title, DatePosted, CompanySlug string
	Locations                               []string
}
type LinkedInFetch func(context.Context, Request) ([]byte, error)
type LinkedInPause func(context.Context) error

//go:embed linkedin_countries.json
var linkedInCountryBytes []byte
var linkedInCountries = func() map[string]string {
	m := map[string]string{}
	if json.Unmarshal(linkedInCountryBytes, &m) != nil {
		panic("invalid frozen LinkedIn country reference")
	}
	return m
}()
var linkedInCompanyPath = regexp.MustCompile(`(?i)^/company/([^/?#]+)/jobs/?$`)
var linkedInCompanyLink = regexp.MustCompile(`(?i)^/company/([^/?#]+)`)
var linkedInJobURN = regexp.MustCompile(`urn:li:jobPosting:([\p{Nd}]+)`)
var linkedInPathID = regexp.MustCompile(`(?:-|/)([\p{Nd}]+)/?$`)
var linkedInCompanyID = regexp.MustCompile(`(?i)facetCurrentCompany(?:%3D|=)([\p{Nd}]+)`)
var linkedInComments = regexp.MustCompile(`(?s)<!--.*?-->`)
var linkedInDoctype = regexp.MustCompile(`(?i)<!DOCTYPE\s+html\s*>`)

func linkedInHost(host string) bool {
	return host == "linkedin.com" || strings.HasSuffix(host, ".linkedin.com")
}
func linkedInURL(raw string) (PythonURL, *url.URL, bool) {
	p, ok := ParsePythonURL(raw)
	if !ok {
		return p, nil, false
	}
	a, err := url.Parse("https://" + p.Host)
	if err != nil || !linkedInHost(strings.ToLower(a.Hostname())) {
		return p, nil, false
	}
	return p, a, true
}
func linkedInDigits(s string) bool {
	if s == "" || len([]rune(s)) > 64 {
		return false
	}
	for _, r := range s {
		if !unicode.Is(unicode.Nd, r) {
			return false
		}
	}
	return true
}
func linkedInSlug(raw string) string {
	p, _, ok := linkedInURL(raw)
	if !ok {
		return ""
	}
	m := linkedInCompanyPath.FindStringSubmatch(p.Path)
	if m == nil {
		return ""
	}
	return m[1]
}
func linkedInIDs(value any) ([]string, error) {
	values := []any{}
	switch x := value.(type) {
	case string:
		values = []any{x}
	case []any:
		values = x
	default:
		return nil, ErrOptions
	}
	if len(values) == 0 || len(values) > 32 {
		return nil, ErrOptions
	}
	out := []string{}
	seen := map[string]bool{}
	for _, v := range values {
		s, ok := v.(string)
		if !ok || !linkedInDigits(s) || seen[s] {
			return nil, ErrOptions
		}
		seen[s] = true
		out = append(out, s)
	}
	return out, nil
}
func LinkedInOptionsFromMetadata(board, raw string) (LinkedInOptions, error) {
	o := LinkedInOptions{ExcludedCountries: map[string]bool{}}
	md, err := DecodeInlineMetadata(raw)
	if err != nil {
		return o, err
	}
	for k := range md {
		switch k {
		case "company_id", "company_ids", "company_slug", "keywords", "canonical_numeric_job_urls", "source_ownership_excluded_country_codes", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return o, ErrOptions
		}
	}
	o.CompanySlug = linkedInSlug(board)
	if v, exists := md["company_slug"]; exists && v != nil && v != "" {
		s, ok := v.(string)
		if !ok || len(s) > 1024 {
			return o, ErrOptions
		}
		o.CompanySlug = s
	}
	if md["company_id"] != nil && md["company_ids"] != nil {
		return o, ErrOptions
	}
	ids := md["company_ids"]
	if ids == nil {
		ids = md["company_id"]
	}
	if ids != nil {
		o.CompanyIDs, err = linkedInIDs(ids)
		if err != nil {
			return o, err
		}
	} else if p, _, ok := linkedInURL(board); ok {
		q, e := url.ParseQuery(p.Query)
		if e != nil {
			return o, ErrOptions
		}
		a := []any{}
		for _, v := range q["f_C"] {
			if v != "" {
				for _, id := range strings.Split(v, ",") {
					a = append(a, id)
				}
			}
		}
		if len(a) > 0 {
			o.CompanyIDs, err = linkedInIDs(a)
			if err != nil {
				return o, err
			}
		}
	}
	if len(o.CompanyIDs) == 0 && o.CompanySlug == "" {
		return o, ErrOptions
	}
	if v := md["keywords"]; v != nil {
		s, ok := v.(string)
		if !ok || strings.TrimSpace(s) == "" || len(s) > 4096 {
			return o, ErrOptions
		}
		o.Keywords = strings.TrimSpace(s)
	}
	if v, exists := md["canonical_numeric_job_urls"]; exists {
		b, ok := v.(bool)
		if !ok {
			return o, ErrOptions
		}
		o.NumericURLs = b
	}
	if v := md["source_ownership_excluded_country_codes"]; v != nil {
		a, ok := v.([]any)
		if !ok || len(a) == 0 || len(a) > 32 {
			return o, ErrOptions
		}
		for _, v := range a {
			s, ok := v.(string)
			if !ok || linkedInCountries[s] == "" {
				return o, ErrOptions
			}
			o.ExcludedCountries[s] = true
		}
	}
	return o, nil
}
func LinkedInListingRequest(ids, keywords string, start int) Request {
	u := "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?location=Worldwide&sortBy=DD&start=" + strconv.Itoa(start)
	if ids != "" {
		u += "&f_C=" + url.QueryEscape(ids)
	}
	if keywords != "" {
		u += "&keywords=" + url.QueryEscape(keywords)
	}
	return Request{Method: "GET", URL: u, Headers: http.Header{"Accept-Language": []string{"en-US,en;q=0.9"}}}
}

func (o LinkedInOptions) ResourceMatches(resource string) bool {
	if len(o.CompanyIDs) == 0 {
		return false
	}
	u, err := url.Parse(resource)
	if err != nil {
		return false
	}
	start, err := strconv.Atoi(u.Query().Get("start"))
	if err != nil || start < 0 || start > 1000 || start%10 != 0 {
		return false
	}
	ids := strings.Join(o.CompanyIDs, ",")
	return resource == LinkedInListingRequest(ids, "", start).URL || o.Keywords != "" && resource == LinkedInListingRequest(ids, o.Keywords, start).URL
}
func LinkedInCanonicalJobURL(id, href string, numeric bool) string {
	if !numeric && href != "" {
		p, a, ok := linkedInURL(href)
		m := linkedInPathID.FindStringSubmatch(p.Path)
		if ok && p.Scheme == "https" && a.User == nil && a.Port() == "" && strings.HasPrefix(p.Path, "/jobs/view/") && m != nil && m[1] == id {
			return "https://www.linkedin.com" + p.Path
		}
	}
	return "https://www.linkedin.com/jobs/view/" + id
}
func linkedInNodeText(n *html.Node) string {
	if n == nil {
		return ""
	}
	var b strings.Builder
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			b.WriteString(inlineTrim(n.Data))
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(n)
	return b.String()
}
func linkedInSelect(n *html.Node, selector string) *html.Node {
	return cascadia.Query(n, cascadia.MustCompile(selector))
}
func linkedInAttr(n *html.Node, key string) string {
	if n == nil {
		return ""
	}
	s, _ := inlineAttribute(n, key)
	return s
}
func ParseLinkedInCards(body []byte, numeric bool) ([]LinkedInListingJob, error) {
	if len(body) > 25_000_000 {
		return nil, ErrInventory
	}
	tree, err := html.Parse(strings.NewReader(string(body)))
	if err != nil {
		return nil, ErrInventory
	}
	jobs := []LinkedInListingJob{}
	seen := map[string]bool{}
	for _, card := range inlineSelectedNodes(tree, ".base-search-card") {
		m := linkedInJobURN.FindStringSubmatch(linkedInAttr(card, "data-entity-urn"))
		if m == nil || seen[m[1]] {
			return nil, ErrInventory
		}
		seen[m[1]] = true
		job := LinkedInListingJob{ID: m[1], URL: LinkedInCanonicalJobURL(m[1], linkedInAttr(linkedInSelect(card, ".base-card__full-link"), "href"), numeric), Title: linkedInNodeText(linkedInSelect(card, ".base-search-card__title")), DatePosted: linkedInAttr(linkedInSelect(card, "time"), "datetime")}
		if loc := linkedInNodeText(linkedInSelect(card, ".job-search-card__location")); loc != "" {
			job.Locations = []string{loc}
		}
		company := linkedInSelect(card, `.base-search-card__subtitle a[href*="/company/"]`)
		if p, _, ok := linkedInURL(linkedInAttr(company, "href")); ok {
			if m := linkedInCompanyLink.FindStringSubmatch(p.Path); m != nil {
				job.CompanySlug = m[1]
			}
		}
		jobs = append(jobs, job)
		if len(jobs) > 1000 {
			return nil, ErrInventory
		}
	}
	return jobs, nil
}
func linkedInEmptyFragment(body []byte) bool {
	s := linkedInComments.ReplaceAllString(string(body), "")
	return inlineTrim(linkedInDoctype.ReplaceAllString(s, "")) == ""
}
func linkedInQuery(ctx context.Context, o LinkedInOptions, keyword string, fetch LinkedInFetch, pause LinkedInPause) ([]LinkedInListingJob, bool, error) {
	jobs := []LinkedInListingJob{}
	seen := map[string]bool{}
	for start := 0; start <= 1000; start += 10 {
		var body []byte
		var page []LinkedInListingJob
		for attempt := 0; attempt < 2; attempt++ {
			if ctx.Err() != nil {
				return nil, false, ctx.Err()
			}
			var err error
			body, err = fetch(ctx, LinkedInListingRequest(strings.Join(o.CompanyIDs, ","), keyword, start))
			if err != nil {
				return nil, false, err
			}
			if body == nil {
				break
			}
			page, err = ParseLinkedInCards(body, o.NumericURLs)
			if err != nil {
				return nil, false, err
			}
			mismatch := false
			for _, j := range page {
				if o.CompanySlug != "" && j.CompanySlug != o.CompanySlug {
					mismatch = true
					break
				}
			}
			if !mismatch {
				break
			}
			if attempt == 1 {
				return nil, false, ErrInventory
			}
			if pause != nil {
				if err = pause(ctx); err != nil {
					return nil, false, err
				}
			}
		}
		if len(page) == 0 {
			if body == nil || linkedInEmptyFragment(body) {
				return jobs, false, nil
			}
			return nil, false, ErrInventory
		}
		for _, j := range page {
			if seen[j.ID] {
				return nil, false, ErrInventory
			}
			seen[j.ID] = true
			jobs = append(jobs, j)
		}
		if len(jobs) >= 1000 {
			return jobs[:1000], true, nil
		}
		if len(page) < 10 {
			return jobs, false, nil
		}
		if pause != nil {
			if err := pause(ctx); err != nil {
				return nil, false, err
			}
		}
	}
	return nil, false, ErrInventory
}
func DiscoverLinkedIn(ctx context.Context, o LinkedInOptions, fetch LinkedInFetch, pause LinkedInPause) (Inventory, error) {
	failure := Inventory{}
	if len(o.CompanyIDs) == 0 {
		body, err := fetch(ctx, LinkedInListingRequest("", strings.ReplaceAll(o.CompanySlug, "-", " "), 0))
		if err != nil {
			return failure, err
		}
		cards, err := ParseLinkedInCards(body, false)
		if err != nil {
			return failure, err
		}
		id := ""
		for _, j := range cards {
			if j.CompanySlug == o.CompanySlug {
				id = j.ID
				break
			}
		}
		if id == "" {
			return failure, ErrInventory
		}
		detail, err := fetch(ctx, Request{Method: "GET", URL: "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + id, Headers: http.Header{"Accept-Language": []string{"en-US,en;q=0.9"}}})
		if err != nil {
			return failure, err
		}
		m := linkedInCompanyID.FindSubmatch(detail)
		if m == nil {
			return failure, ErrInventory
		}
		o.CompanyIDs = []string{string(m[1])}
	}
	jobs, truncated, err := linkedInQuery(ctx, o, "", fetch, pause)
	if err != nil {
		return failure, err
	}
	if !truncated && o.Keywords != "" {
		if pause != nil {
			if err = pause(ctx); err != nil {
				return failure, err
			}
		}
		recovered, partial, e := linkedInQuery(ctx, o, o.Keywords, fetch, pause)
		if e != nil {
			return failure, e
		}
		seen := map[string]bool{}
		for _, j := range jobs {
			seen[j.ID] = true
		}
		for _, j := range recovered {
			if !seen[j.ID] {
				seen[j.ID] = true
				jobs = append(jobs, j)
			}
		}
		truncated = partial || len(jobs) >= 1000
		if len(jobs) > 1000 {
			jobs = jobs[:1000]
		}
	}
	out := Inventory{Jobs: []Job{}, Truncated: truncated || o.Keywords != ""}
	for _, j := range jobs {
		md := map[string]any{"job_id": j.ID}
		if len(o.CompanyIDs) == 1 {
			md["linkedin_company_id"] = o.CompanyIDs[0]
		} else {
			md["linkedin_company_ids"] = append([]string{}, o.CompanyIDs...)
		}
		if j.CompanySlug != "" {
			md["linkedin_company_slug"] = j.CompanySlug
		}
		if len(o.ExcludedCountries) > 0 {
			country := ""
			if len(j.Locations) == 0 {
				return failure, ErrInventory
			}
			for _, loc := range j.Locations {
				_, tail, ok := strings.Cut(loc, ",")
				if !ok {
					return failure, ErrInventory
				}
				if i := strings.LastIndex(tail, ","); i >= 0 {
					tail = tail[i+1:]
				}
				name := cases.Fold().String(strings.TrimSpace(tail))
				matched := ""
				for code, label := range linkedInCountries {
					if cases.Fold().String(label) == name {
						if matched != "" {
							return failure, ErrInventory
						}
						matched = code
					}
				}
				if matched == "" || country != "" && matched != country {
					return failure, ErrInventory
				}
				country = matched
			}
			if o.ExcludedCountries[country] {
				continue
			}
			md["location_country_code"] = country
		}
		job := Job{URL: j.URL, Locations: j.Locations, Metadata: md}
		if j.Title != "" {
			job.Title = j.Title
		}
		if j.DatePosted != "" {
			job.DatePosted = j.DatePosted
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
