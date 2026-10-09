package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type JobStreetOptions struct{ Host, CompanyID, OrganisationID, Locale, SiteKey string }
type JobStreetJob struct {
	Fields      map[string]any
	SalaryLabel string
}
type JobStreetInventory struct {
	Jobs      []JobStreetJob
	Truncated bool
}

var jobStreetCompanyPath = regexp.MustCompile(`(?i)^/companies/[a-z0-9][a-z0-9._-]*-([0-9]{12,18})(?:/jobs)?/?$`)
var jobStreetJobPath = regexp.MustCompile(`(?i)^/job/([0-9]{1,18})/?$`)
var jobStreetID = regexp.MustCompile(`^[0-9]{1,18}$`)

func jobStreetURL(raw string) (*url.URL, error) {
	u, e := url.Parse(raw)
	if e != nil || len(raw) > 8192 || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || strings.Contains(u.RawPath, "%") {
		return nil, ErrOptions
	}
	if h := strings.ToLower(u.Hostname()); h != "my.jobstreet.com" && h != "sg.jobstreet.com" {
		return nil, ErrOptions
	}
	return u, nil
}
func JobStreetOptionsFromMetadata(board, raw string) (JobStreetOptions, error) {
	o := JobStreetOptions{}
	u, e := jobStreetURL(board)
	if e != nil {
		return o, e
	}
	match := jobStreetCompanyPath.FindStringSubmatch(u.Path)
	if match == nil {
		return o, ErrOptions
	}
	o.Host, o.CompanyID = strings.ToLower(u.Hostname()), match[1]
	o.SiteKey = strings.SplitN(o.Host, ".", 2)[0]
	o.Locale = "en-" + strings.ToUpper(o.SiteKey)
	m, e := DecodeInlineMetadata(raw)
	if e != nil {
		return o, e
	}
	for k, v := range m {
		switch k {
		case "host":
			if v != o.Host {
				return o, ErrOptions
			}
		case "company_id":
			if v != o.CompanyID {
				return o, ErrOptions
			}
		case "organisation_id":
			s, ok := v.(string)
			if !ok || !jobStreetID.MatchString(s) {
				return o, ErrOptions
			}
			o.OrganisationID = s
		case "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}

const jobStreetCompanyQuery = "\nquery Company($id: CompanyId!) {\n  companyDetails(id: $id) {\n    companyProfile {\n      organisationId\n    }\n  }\n}\n"
const jobStreetDetailQuery = "\nquery Job($id: ID!, $locale: Locale!) {\n  jobDetails(id: $id) {\n    job {\n      id\n      title\n      content\n      isExpired\n      status\n      location { label(locale: $locale, type: LONG) }\n      workTypes { label(locale: $locale) }\n      salary { label }\n      advertiser { name(locale: $locale) }\n      createdAt { dateTimeUtc }\n      expiresAt { dateTimeUtc }\n    }\n  }\n}\n"

func jobStreetGraphQL(host, query string, variables map[string]string) Request {
	body, _ := json.Marshal(map[string]any{"query": query, "variables": variables})
	return Request{Method: "POST", URL: "https://" + host + "/graphql", Body: string(body), Headers: http.Header{"Accept": {"application/json"}, "Content-Type": {"application/json"}}}
}
func (o JobStreetOptions) CompanyRequest() Request {
	return jobStreetGraphQL(o.Host, jobStreetCompanyQuery, map[string]string{"id": o.CompanyID})
}
func (o JobStreetOptions) PageRequest(page int) Request {
	q := url.Values{"companyid": {o.OrganisationID}, "page": {strconv.Itoa(page)}, "pagesize": {"100"}, "siteKey": {o.SiteKey}, "source": {"COMPANY"}, "locale": {o.Locale}, "include": {"nofeatured"}, "sortMode": {"Relevance"}}
	return Request{Method: "GET", URL: "https://" + o.Host + "/api/jobsearch/v5/search?" + q.Encode(), Headers: http.Header{"Accept": {"application/json"}}}
}
func (o JobStreetOptions) ResourceMatches(resource string) bool {
	if resource == o.CompanyRequest().URL {
		return true
	}
	u, e := url.Parse(resource)
	if e != nil {
		return false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q["page"]) != 1 {
		return false
	}
	page, e := strconv.Atoi(q.Get("page"))
	return e == nil && page >= 1 && page <= 100 && resource == o.PageRequest(page).URL
}
func jobStreetObject(body []byte) (map[string]any, error) {
	if len(body) > 64<<20 {
		return nil, ErrInventory
	}
	d, e := Decode(body)
	if e != nil {
		return nil, e
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return m, nil
}
func jobStreetData(body []byte) (map[string]any, error) {
	m, e := jobStreetObject(body)
	if e != nil {
		return nil, e
	}
	if detailTruthy(m["errors"]) {
		return nil, ErrInventory
	}
	data, ok := m["data"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return data, nil
}
func jobStreetMap(v any) map[string]any { m, _ := v.(map[string]any); return m }
func jobStreetScalar(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if n, ok := v.(json.Number); ok && n != "0" {
		return string(n)
	}
	return ""
}
func jobStreetClean(v any) string { s, _ := v.(string); return strings.Join(strings.Fields(s), " ") }
func jobStreetIterable(v any) ([]any, error) {
	if !detailTruthy(v) {
		return nil, nil
	}
	switch v := v.(type) {
	case []any:
		return v, nil
	case string, map[string]any:
		return nil, nil
	}
	return nil, ErrInventory
}
func jobStreetCount(v any) (int, error) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, ErrInventory
	}
	i, e := strconv.ParseInt(string(n), 10, 64)
	if e != nil || i < 0 || i > int64(^uint(0)>>1) {
		return 0, ErrInventory
	}
	return int(i), nil
}
func jobStreetSummary(row map[string]any, o JobStreetOptions) (JobStreetJob, error) {
	id, title := jobStreetScalar(row["id"]), jobStreetClean(row["title"])
	if !jobStreetID.MatchString(id) || title == "" {
		return JobStreetJob{}, ErrInventory
	}
	md := map[string]any{"jobstreet_job_id": id, "jobstreet_company_id": o.CompanyID}
	employer := jobStreetMap(row["employer"])
	if s := jobStreetClean(employer["name"]); s != "" {
		md["employer"] = s
	}
	fields := map[string]any{"url": "https://" + o.Host + "/job/" + id, "title": title, "language": "en", "metadata": md}
	locations := []string{}
	seen := map[string]bool{}
	if detailTruthy(row["locations"]) {
		rows, e := jobStreetIterable(row["locations"])
		if e != nil {
			return JobStreetJob{}, ErrInventory
		}
		for _, raw := range rows {
			if s := jobStreetClean(jobStreetMap(raw)["label"]); s != "" && !seen[s] {
				locations = append(locations, s)
				seen[s] = true
			}
		}
	}
	if len(locations) > 0 {
		fields["locations"] = locations
	}
	if work, ok := row["workTypes"].([]any); ok {
		for _, raw := range work {
			if s := jobStreetClean(raw); s != "" {
				fields["employment_type"] = s
				break
			}
		}
	}
	if arrangements, ok := jobStreetMap(row["workArrangements"])["data"].([]any); ok {
		for _, raw := range arrangements {
			s, e := providerLocationType(jobStreetMap(jobStreetMap(raw)["label"])["text"])
			if e == nil && s != "" {
				fields["job_location_type"] = s
				break
			}
		}
	}
	if date := jobStreetClean(row["listingDate"]); date != "" {
		fields["date_posted"] = date
	}
	classifications := []string{}
	seen = map[string]bool{}
	if detailTruthy(row["classifications"]) {
		rows, e := jobStreetIterable(row["classifications"])
		if e != nil {
			return JobStreetJob{}, ErrInventory
		}
		for _, raw := range rows {
			m := jobStreetMap(raw)
			for _, k := range []string{"classification", "subclassification", "subClassification"} {
				if s := jobStreetClean(jobStreetMap(m[k])["description"]); s != "" && !seen[s] {
					classifications = append(classifications, s)
					seen[s] = true
				}
			}
		}
	}
	if len(classifications) > 0 {
		md["classifications"] = classifications
	}
	return JobStreetJob{Fields: fields, SalaryLabel: jobStreetClean(row["salaryLabel"])}, nil
}
func ParseJobStreetPage(body []byte, o JobStreetOptions, page int) ([]JobStreetJob, int, error) {
	m, e := jobStreetObject(body)
	if e != nil {
		return nil, 0, e
	}
	rows, ok := m["data"].([]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	total, e := jobStreetCount(m["totalCount"])
	if e != nil {
		return nil, 0, e
	}
	md := jobStreetMap(m["solMetadata"])
	pn, e1 := jobStreetCount(md["pageNumber"])
	ps, e2 := jobStreetCount(md["pageSize"])
	pt, e3 := jobStreetCount(md["totalJobCount"])
	if page < 1 || page > 100 || e1 != nil || e2 != nil || e3 != nil || pn != page || ps != 100 || pt != total || len(rows) != min(100, max(0, total-(page-1)*100)) {
		return nil, 0, ErrInventory
	}
	jobs := []JobStreetJob{}
	seen := map[string]bool{}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, 0, ErrInventory
		}
		owner := jobStreetMap(row["employer"])
		id := jobStreetScalar(row["id"])
		if !jobStreetID.MatchString(id) || seen[id] || jobStreetScalar(owner["id"]) != o.OrganisationID || jobStreetScalar(owner["companyId"]) != o.CompanyID {
			return nil, 0, ErrInventory
		}
		seen[id] = true
		job, e := jobStreetSummary(row, o)
		if e != nil {
			return nil, 0, e
		}
		jobs = append(jobs, job)
	}
	return jobs, total, nil
}
func DiscoverJobStreet(ctx context.Context, o JobStreetOptions, fetch SmallProviderFetch) (JobStreetInventory, error) {
	out := JobStreetInventory{Jobs: []JobStreetJob{}}
	if ctx == nil || fetch == nil {
		return out, ErrOptions
	}
	if ctx.Err() != nil {
		return out, ctx.Err()
	}
	if o.OrganisationID == "" {
		body, e := fetch(ctx, o.CompanyRequest())
		if e != nil {
			return out, e
		}
		data, e := jobStreetData(body)
		if e != nil {
			return out, e
		}
		id, ok := jobStreetMap(jobStreetMap(data["companyDetails"])["companyProfile"])["organisationId"].(string)
		if !ok || !jobStreetID.MatchString(id) {
			return out, ErrInventory
		}
		o.OrganisationID = id
	}
	seen := map[string]bool{}
	total := -1
	for page := 1; page <= 100; page++ {
		if ctx.Err() != nil {
			return JobStreetInventory{}, ctx.Err()
		}
		body, e := fetch(ctx, o.PageRequest(page))
		if e != nil {
			return JobStreetInventory{}, e
		}
		jobs, count, e := ParseJobStreetPage(body, o, page)
		if e != nil || total >= 0 && count != total {
			return JobStreetInventory{}, ErrInventory
		}
		total = count
		for _, job := range jobs {
			source := job.Fields["url"].(string)
			if seen[source] {
				return JobStreetInventory{}, ErrInventory
			}
			seen[source] = true
			out.Jobs = append(out.Jobs, job)
		}
		if len(out.Jobs) == min(total, 10000) {
			out.Truncated = total > 10000
			return out, ctx.Err()
		}
	}
	return JobStreetInventory{}, ErrInventory
}
func JobStreetDetailRequest(source string, config map[string]any) (Request, string, string, error) {
	u, e := jobStreetURL(source)
	if e != nil || len(config) != 0 {
		return Request{}, "", "", ErrOptions
	}
	match := jobStreetJobPath.FindStringSubmatch(u.Path)
	if match == nil {
		return Request{}, "", "", ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	locale := "en-MY"
	if host == "sg.jobstreet.com" {
		locale = "en-SG"
	}
	return jobStreetGraphQL(host, jobStreetDetailQuery, map[string]string{"id": match[1], "locale": locale}), host, match[1], nil
}
func ParseJobStreetDetail(body []byte, host, id string) (JobStreetJob, error) {
	data, e := jobStreetData(body)
	if e != nil {
		return JobStreetJob{}, e
	}
	if data["jobDetails"] == nil {
		return JobStreetJob{Fields: map[string]any{}}, nil
	}
	job, ok := jobStreetMap(data["jobDetails"])["job"].(map[string]any)
	if !ok || jobStreetScalar(job["id"]) != id {
		return JobStreetJob{}, ErrInventory
	}
	if job["isExpired"] == true || strings.ToLower(jobStreetScalar(job["status"])) != "active" {
		return JobStreetJob{Fields: map[string]any{}}, nil
	}
	title := jobStreetClean(job["title"])
	desc, ok := job["content"].(string)
	if title == "" || !ok || strings.TrimSpace(desc) == "" {
		return JobStreetJob{}, ErrInventory
	}
	md := map[string]any{"jobstreet_job_id": id}
	fields := map[string]any{"title": title, "description": desc, "language": "en", "metadata": md}
	if loc := jobStreetClean(jobStreetMap(job["location"])["label"]); loc != "" {
		fields["locations"] = []string{loc}
	}
	if work := jobStreetClean(jobStreetMap(job["workTypes"])["label"]); work != "" {
		fields["employment_type"] = work
	}
	if date := jobStreetClean(jobStreetMap(job["createdAt"])["dateTimeUtc"]); date != "" {
		fields["date_posted"] = date
	}
	if employer := jobStreetClean(jobStreetMap(job["advertiser"])["name"]); employer != "" {
		md["employer"] = employer
	}
	if expires := jobStreetClean(jobStreetMap(job["expiresAt"])["dateTimeUtc"]); expires != "" {
		md["expiration_date"] = expires
	}
	return JobStreetJob{Fields: fields, SalaryLabel: jobStreetClean(jobStreetMap(job["salary"])["label"])}, nil
}
