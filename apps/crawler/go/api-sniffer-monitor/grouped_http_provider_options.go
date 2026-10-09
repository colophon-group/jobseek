package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
)

const CuratelyAPIBase = "https://api.curately.ai/QADemoCurately"
const CuratelySearchURL = CuratelyAPIBase + "/sovrenjobsearch"
const InploiAPIURL = "https://api.inploi.com/search/results"

var curatelyBoardPath = regexp.MustCompile(`(?i)^/jobs/([a-z0-9]+(?:-[a-z0-9]+)*)(?:/.*)?$`)
var jobConvoListingPath = regexp.MustCompile(`(?i)^/([a-z]{2}-[a-z]{2})/careers/([^/]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$`)
var jobConvoJobPath = regexp.MustCompile(`(?i)^/job/([A-Za-z0-9_-]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$`)
var jobConvoLocale = regexp.MustCompile(`(?i)^[a-z]{2}(?:-[a-z]{2})?$`)
var inploiPublicKey = regexp.MustCompile(`\bpk_[A-Za-z0-9_-]{12,}\b`)

func groupedHTTPOptions(o *FinalHTTPProviderOptions, u *url.URL, m map[string]any, allowed map[string]bool) error {
	text := func(key string, dst *string) error {
		allowed[key] = true
		if v, ok := m[key]; ok && v != nil {
			s, ok := v.(string)
			if !ok || len(s) > 8192 || strings.ContainsAny(s, "\x00\r\n") {
				return ErrOptions
			}
			*dst = s
		}
		return nil
	}
	number := func(key string, dst *int, defaultValue, min, max int, text bool) error {
		allowed[key] = true
		*dst = defaultValue
		if v, ok := m[key]; ok {
			n, e := smallInt(v, text)
			if e != nil || n < min || n > max {
				return ErrOptions
			}
			*dst = n
		}
		return nil
	}
	switch o.Provider {
	case "curately":
		match := curatelyBoardPath.FindStringSubmatch(strings.TrimRight(u.Path, "/"))
		if !strings.EqualFold(u.Hostname(), "careers.curately.ai") || u.RawQuery != "" || len(match) != 2 {
			return ErrOptions
		}
		o.Tenant = strings.ToLower(match[1])
		allowed["short_name"] = true
		if v, ok := m["short_name"]; ok && v != nil && v != o.Tenant {
			return ErrOptions
		}
		allowed["client_id"] = true
		if v, ok := m["client_id"]; ok && v != nil {
			n, e := smallInt(v, true)
			if e != nil || n < 1 {
				return ErrOptions
			}
			o.ClientID = n
		}
		for _, v := range []struct {
			k string
			d *string
		}{{"currency", &o.Currency}, {"salary_unit", &o.SalaryUnit}, {"language", &o.Language}} {
			if text(v.k, v.d) != nil {
				return ErrOptions
			}
		}
		if number("days_back", &o.DaysBack, 180, 1, 3650, false) != nil || number("snapshot_attempts", &o.SnapshotAttempts, 3, 1, 5, false) != nil || number("semantic_zero_attempts", &o.SemanticZeroAttempts, 3, 1, 5, false) != nil {
			return ErrOptions
		}
	case "inploi":
		for _, v := range []struct {
			k string
			d *string
		}{{"api_key", &o.APIKey}, {"segment_id", &o.SegmentID}, {"job_url_template", &o.JobURLTemplate}} {
			if text(v.k, v.d) != nil {
				return ErrOptions
			}
		}
		if o.APIKey != "" && !inploiPublicKey.MatchString(o.APIKey) || o.SegmentID != "" && !smallUnsigned.MatchString(o.SegmentID) {
			return ErrOptions
		}
		allowed["search_url"], allowed["jobs"] = true, true
		if number("page_size", &o.PageSize, 5000, 0, 1_000_000_000, true) != nil {
			return ErrOptions
		}
		o.PageSize = max(1, min(50000, o.PageSize))
		if o.JobURLTemplate != "" {
			if strings.Count(o.JobURLTemplate, "{id}") != 1 || strings.ContainsAny(strings.ReplaceAll(o.JobURLTemplate, "{id}", ""), "{}") {
				return ErrOptions
			}
			candidate, e := url.Parse(strings.ReplaceAll(o.JobURLTemplate, "{id}", "123"))
			if e != nil || candidate.Scheme != u.Scheme || candidate.Host != u.Host || candidate.User != nil || candidate.Fragment != "" {
				return ErrOptions
			}
		}
	case "jobconvo":
		allowed["listing_url"] = true
		if v, ok := m["listing_url"]; ok && detailTruthy(v) {
			s, ok := v.(string)
			if !ok {
				return ErrOptions
			}
			var e error
			u, e = url.Parse(s)
			if e != nil {
				return ErrOptions
			}
		}
		match := jobConvoListingPath.FindStringSubmatch(u.Path)
		if len(match) != 4 || !jobConvoHost(u) || u.Scheme != "https" || u.User != nil || u.Port() != "" && u.Port() != "443" {
			return ErrOptions
		}
		o.Locale, o.Tenant, o.CareerPage = strings.ToLower(match[1]), match[2], strings.ToLower(match[3])
		for _, p := range [][2]string{{"locale", o.Locale}, {"career_page", o.CareerPage}} {
			allowed[p[0]] = true
			if v, ok := m[p[0]]; ok && v != nil {
				s, ok := v.(string)
				if !ok || strings.ToLower(s) != p[1] {
					return ErrOptions
				}
			}
		}
		u.Fragment, u.RawFragment = "", ""
		u.Host = strings.ToLower(u.Host)
		o.BoardURL = u.String()
		o.Origin = u.Scheme + "://" + u.Host
	}
	return nil
}

func jobConvoHost(u *url.URL) bool {
	return strings.EqualFold(u.Hostname(), "app.jobconvo.com") || strings.EqualFold(u.Hostname(), "jobs.jobconvo.com")
}

func (o FinalHTTPProviderOptions) groupedJobMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	switch o.Provider {
	case "curately":
		prefix := "https://careers.curately.ai/jobs/" + o.Tenant + "/apply-job/"
		id := strings.TrimSuffix(strings.TrimPrefix(source, prefix), "/job")
		return smallUnsigned.MatchString(id) && strings.TrimLeft(id, "0") != "" && source == prefix+id+"/job"
	case "inploi":
		base, _ := url.Parse(o.BoardURL)
		if u.Host != base.Host {
			return false
		}
		if o.JobURLTemplate == "" {
			return strings.HasPrefix(u.Path, "/job/") && len(strings.TrimPrefix(u.Path, "/job/")) > 0 && u.RawQuery == ""
		}
		parts := strings.Split(o.JobURLTemplate, "{id}")
		return len(parts) == 2 && strings.HasPrefix(source, parts[0]) && strings.HasSuffix(source, parts[1]) && len(source) > len(parts[0])+len(parts[1])
	case "jobconvo":
		return u.Host == "app.jobconvo.com" && jobConvoJobPath.MatchString(u.Path) && u.RawQuery == ""
	}
	return false
}

func (o FinalHTTPProviderOptions) groupedResourceMatches(source string) bool {
	if o.Provider == "inploi" {
		base, _ := url.Parse(o.BoardURL)
		if source == o.BoardURL || source == base.Scheme+"://"+base.Host+"/search" {
			return true
		}
		u, e := url.Parse(source)
		if e != nil || u.Scheme+"://"+u.Host+u.Path != InploiAPIURL || u.User != nil || u.Fragment != "" {
			return false
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) != 4 || len(q["filters[segment_ids][0]"]) != 1 || len(q["query"]) != 1 || q.Get("query") != "" || len(q["page"]) != 1 || len(q["per_page"]) != 1 {
			return false
		}
		if !smallUnsigned.MatchString(q.Get("filters[segment_ids][0]")) || o.SegmentID != "" && q.Get("filters[segment_ids][0]") != o.SegmentID {
			return false
		}
		page, e := smallInt(q.Get("page"), true)
		size, se := smallInt(q.Get("per_page"), true)
		return e == nil && se == nil && page >= 1 && page <= 50000 && size >= 1 && size <= 50000
	}
	base, _ := url.Parse(o.BoardURL)
	u, e := url.Parse(source)
	if e != nil || !jobConvoHost(u) || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || u.Path != base.Path {
		return false
	}
	if source == o.BoardURL || u.RawQuery == base.RawQuery {
		return true
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) != 1 || len(q["page"]) != 1 {
		return false
	}
	page, e := smallInt(q.Get("page"), true)
	return e == nil && page >= 1 && page <= 1000
}
