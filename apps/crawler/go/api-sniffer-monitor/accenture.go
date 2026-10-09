package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type AccentureOptions struct{ Country, Language, Site, Endpoint string }

const AccentureFindJobs = "elastic/findjobs"
const AccentureJobSearch = "jobsearch/result"
const accentureBoundary = "----FormBoundary"

var accentureSite = regexp.MustCompile(`^[a-z]{2}-[a-z]{2}$`)

func AccentureOptionsFromMetadata(board, raw string) (AccentureOptions, error) {
	u, e := url.Parse(board)
	m, f := DecodeInlineMetadata(raw)
	o := AccentureOptions{Endpoint: AccentureFindJobs}
	if e != nil || f != nil || u.Scheme != "https" || u.Host != "www.accenture.com" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return o, ErrOptions
	}
	for k, v := range m {
		s, ok := v.(string)
		if !ok || s == "" || len(s) > 256 || strings.ContainsAny(s, "\r\n\x00") {
			return o, ErrOptions
		}
		switch k {
		case "country":
			o.Country = s
		case "language":
			o.Language = s
		case "site":
			o.Site = s
		case "endpoint":
			o.Endpoint = s
		default:
			return o, ErrOptions
		}
	}
	if !accentureSite.MatchString(o.Site) || o.Country == "" || o.Language != strings.Split(o.Site, "-")[1] || (o.Endpoint != AccentureFindJobs && o.Endpoint != AccentureJobSearch) || u.Path != "/"+o.Site+"/careers/jobsearch" {
		return o, ErrOptions
	}
	return o, nil
}

func (o AccentureOptions) FindJobsRequest(offset int, filters []map[string]any) Request {
	fields := [][2]string{{"startIndex", strconv.Itoa(offset)}, {"maxResultSize", "500"}, {"jobKeyword", ""}, {"jobCountry", o.Country}, {"jobLanguage", o.Language}, {"countrySite", o.Site}, {"sortBy", "2"}, {"totalHits", "true"}}
	if len(filters) > 0 {
		b, _ := json.Marshal(filters)
		fields = append(fields, [2]string{"jobFilters", string(b)})
	}
	parts := []string{}
	for _, p := range fields {
		parts = append(parts, "--"+accentureBoundary+"\r\nContent-Disposition: form-data; name=\""+p[0]+"\"\r\n\r\n"+p[1])
	}
	return Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/" + AccentureFindJobs, Body: strings.Join(parts, "\r\n") + "\r\n--" + accentureBoundary + "--", Headers: http.Header{"Content-Type": {"multipart/form-data; boundary=" + accentureBoundary}}}
}

func AccentureJob(raw map[string]any, o AccentureOptions) (Job, bool, error) {
	key := "guid"
	if o.Endpoint == AccentureJobSearch {
		key = "jobDetailUrl"
	}
	if !detailTruthy(raw[key]) {
		return Job{}, false, nil
	}
	j := Job{Title: raw["title"], DatePosted: raw["postedDate"]}
	location := raw["location"]
	if o.Endpoint == AccentureFindJobs {
		j.URL = "https://www.accenture.com/" + o.Site + "/careers/jobdetails?id=" + pythonString(raw[key])
		j.Description = raw["jobDescription"]
		j.JobLocationType = raw["remoteType"]
		j.Metadata = map[string]any{}
		for _, k := range []string{"businessArea", "careerLevel", "guid"} {
			if detailTruthy(raw[k]) {
				j.Metadata[k] = raw[k]
			}
		}
	} else {
		s, ok := raw[key].(string)
		if !ok {
			return Job{}, false, ErrInventory
		}
		j.URL = s
		if strings.HasPrefix(s, "/") {
			j.URL = "https://www.accenture.com" + s
		}
		location = raw["jobCityState"]
	}
	if detailTruthy(location) {
		if s, ok := location.(string); ok {
			j.Locations = []string{s}
		} else if a, ok := location.([]any); ok {
			j.Locations = []string{}
			for _, v := range a {
				s, ok := v.(string)
				if !ok {
					return Job{}, false, ErrInventory
				}
				j.Locations = append(j.Locations, s)
			}
		} else {
			return Job{}, false, ErrInventory
		}
	}
	return j, true, nil
}

func AccenturePage(d *Document) ([]map[string]any, int, error) {
	if d == nil {
		return nil, 0, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	v := m["totalHits"]
	if total, ok := v.(map[string]any); ok {
		v = total["total"]
		if !detailTruthy(v) {
			v = total["value"]
		}
	}
	n := 0
	if detailTruthy(v) {
		var valid bool
		n, valid = darwinboxInteger(v)
		if !valid {
			return nil, 0, ErrInventory
		}
	}
	if !detailTruthy(m["data"]) {
		return []map[string]any{}, n, nil
	}
	a, ok := m["data"].([]any)
	if !ok || len(a) > 500 {
		return nil, 0, ErrInventory
	}
	rows := []map[string]any{}
	for _, v := range a {
		m, ok := v.(map[string]any)
		if !ok {
			return nil, 0, ErrInventory
		}
		rows = append(rows, m)
	}
	return rows, n, nil
}

func AccentureCapturedRequest(template Request, offset int) (Request, error) {
	if template.Method != "POST" || template.URL != "https://www.accenture.com/api/accenture/"+AccentureJobSearch || offset < 0 || offset >= 50000 || offset%500 != 0 || len(template.Body) > 1<<20 {
		return Request{}, ErrOptions
	}
	var e error
	template.Body, e = accentureCapturedBodyParam(template.Body, "startIndex", offset)
	if e != nil {
		return Request{}, e
	}
	template.Body, e = accentureCapturedBodyParam(template.Body, "maxResultSize", 500)
	return template, e
}

func accentureCapturedBodyParam(body, key string, value int) (string, error) {
	if !strings.HasPrefix(body, "----") {
		if _, e := Decode([]byte(body)); e == nil {
			return setBodyParam(body, key, value)
		}
		parts := strings.Split(body, "&")
		if len(parts) > 1024 {
			return "", ErrOptions
		}
		out := []string{}
		found := false
		for _, part := range parts {
			if part == "" {
				continue
			}
			k, v, _ := strings.Cut(part, "=")
			k, e := url.QueryUnescape(k)
			if e != nil {
				return "", ErrOptions
			}
			v, e = url.QueryUnescape(v)
			if e != nil {
				return "", ErrOptions
			}
			if k == key {
				v = strconv.Itoa(value)
				found = true
			}
			out = append(out, url.QueryEscape(k)+"="+url.QueryEscape(v))
		}
		if !found {
			return "", ErrOptions
		}
		return strings.Join(out, "&"), nil
	}
	boundary := regexp.MustCompile(`^-{5,6}[A-Za-z0-9_]+`).FindString(body)
	if boundary == "" {
		return "", ErrOptions
	}
	prefix := boundary + "\r\nContent-Disposition: form-data; name=\"" + key + "\"\r\n\r\n"
	start := strings.Index(body, prefix)
	if start < 0 {
		return "", ErrOptions
	}
	start += len(prefix)
	end := strings.Index(body[start:], "\r\n"+boundary)
	if end < 0 {
		return "", ErrOptions
	}
	return body[:start] + strconv.Itoa(value) + body[start+end:], nil
}

// The cosmetic10000 total does not terminate pagination: preserve the original
// 100-page search ceiling. Capped inventories never gain absence authority.
func DiscoverAccenture(ctx context.Context, o AccentureOptions, fetch Fetch, captured *Request, emit func([]Job) error) (Inventory, error) {
	out := Inventory{Jobs: []Job{}}
	if fetch == nil || (o.Endpoint == AccentureJobSearch && captured == nil) {
		return out, ErrOptions
	}
	requests := 0
	page := func(offset int, filters []map[string]any) ([]map[string]any, int, error) {
		if ctx.Err() != nil {
			return nil, 0, ctx.Err()
		}
		requests++
		if requests > 10000 {
			return nil, 0, ErrInventory
		}
		r := o.FindJobsRequest(offset, filters)
		if o.Endpoint == AccentureJobSearch {
			var e error
			r, e = AccentureCapturedRequest(*captured, offset)
			if e != nil {
				return nil, 0, e
			}
		}
		d, e := fetch(ctx, r)
		if e != nil {
			return nil, 0, e
		}
		return AccenturePage(d)
	}
	paginate := func(filters []map[string]any) ([]map[string]any, bool, error) {
		rows, total, e := page(0, filters)
		if e != nil {
			return nil, false, e
		}
		if len(rows) == 0 {
			if total != 0 {
				return nil, false, ErrInventory
			}
			return rows, false, nil
		}
		ceiling := total
		if total >= 10000 {
			ceiling = 50000
		}
		for offset := 500; offset < ceiling; offset += 500 {
			next, _, e := page(offset, filters)
			if e != nil {
				return nil, false, e
			}
			rows = append(rows, next...)
		}
		if total < 10000 && len(rows) != total {
			return nil, false, ErrInventory
		}
		return rows, len(rows) >= 50000, nil
	}
	seen := map[string]bool{}
	key := "guid"
	if o.Endpoint == AccentureJobSearch {
		key = "jobDetailUrl"
	}
	appendJobs := func(rows []map[string]any, deduplicate bool) error {
		batch := []Job{}
		for _, row := range rows {
			if deduplicate {
				if !detailTruthy(row[key]) {
					continue
				}
				id := pythonString(row[key])
				if seen[id] {
					continue
				}
				seen[id] = true
			}
			j, valid, e := AccentureJob(row, o)
			if e != nil {
				return e
			}
			if valid {
				batch = append(batch, j)
			}
		}
		if len(out.Jobs)+len(batch) > 1000000 {
			return ErrInventory
		}
		out.Jobs = append(out.Jobs, batch...)
		if emit != nil && len(batch) > 0 {
			return emit(batch)
		}
		return nil
	}
	initial, capped, e := paginate(nil)
	if e != nil {
		return out, e
	}
	if !capped || o.Endpoint == AccentureJobSearch {
		out.Truncated = capped
		e = appendJobs(initial, false)
		return out, e
	}
	out.Truncated = true
	if e = appendJobs(initial, true); e != nil {
		return out, e
	}
	values := func(rows []map[string]any, key string) ([]string, error) {
		set := map[string]bool{}
		for _, r := range rows {
			if !detailTruthy(r[key]) {
				continue
			}
			s, ok := r[key].(string)
			if !ok {
				return nil, ErrInventory
			}
			set[s] = true
		}
		a := []string{}
		for s := range set {
			a = append(a, s)
		}
		sort.Strings(a)
		return a, nil
	}
	filter := func(key, value string) map[string]any {
		return map[string]any{"fieldName": key + ".keyword", "items": []string{value}, "multiSelect": false}
	}
	areas, e := values(initial, "businessArea")
	if e != nil {
		return out, e
	}
	for _, area := range areas {
		filters := []map[string]any{filter("businessArea", area)}
		rows, capped, e := paginate(filters)
		if e != nil {
			return out, e
		}
		if capped {
			levels, e := values(rows, "careerLevel")
			if e != nil {
				return out, e
			}
			if len(levels) > 0 {
				if e = appendJobs(rows, true); e != nil {
					return out, e
				}
				for _, level := range levels {
					rows, _, e := paginate(append(filters, filter("careerLevel", level)))
					if e != nil {
						return out, e
					}
					if e = appendJobs(rows, true); e != nil {
						return out, e
					}
				}
				continue
			}
		}
		if e = appendJobs(rows, true); e != nil {
			return out, e
		}
	}
	return out, nil
}
