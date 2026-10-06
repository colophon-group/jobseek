package apisniffer

import (
	_ "embed"
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

type ADPDetailOptions struct {
	Base, ID, CID, CCID, Locale string
	Config                      map[string]any `json:"-"`
}

var adpSafeToken = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)
var adpTag = regexp.MustCompile(`<[^>]+>`)
var adpAttachmentPlaceholder = regexp.MustCompile(`(?i)\b(?:see|refer to)\s+(?:the\s+)?attached\s+(?:job\s+)?description\b`)

func ADPDetailRoute(source string, config map[string]any) (ADPDetailOptions, error) {
	out := ADPDetailOptions{}
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || len(source) > 4096 || u.Hostname() != "workforcenow.adp.com" {
		return out, ErrOptions
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) > 32 {
		return out, ErrOptions
	}
	id := q.Get("jobId")
	if id == "" {
		id = q.Get("itemId")
	}
	cid, cc := q.Get("cid"), q.Get("ccId")
	loc := q.Get("lang")
	if loc == "" {
		loc = q.Get("locale")
	}
	if loc == "" {
		loc = "en_US"
	}
	if v, exists := config["locale"]; exists {
		s, ok := v.(string)
		if !ok || !adpSafeToken.MatchString(s) {
			return out, ErrOptions
		}
		loc = s
	}
	for _, value := range []string{id, cid, cc, loc} {
		if !adpSafeToken.MatchString(value) {
			return out, ErrOptions
		}
	}
	prefix, _, ok := strings.Cut(u.Path, "/mdf/recruitment/")
	if !ok || len(prefix) > 256 || strings.ContainsAny(prefix, "\\\r\n\x00") {
		return out, ErrOptions
	}
	for _, p := range strings.Split(prefix, "/") {
		if p == "." || p == ".." {
			return out, ErrOptions
		}
	}
	if v, exists := config["title_location_pattern"]; exists && v != nil {
		s, ok := v.(string)
		if !ok || s == "" || len([]rune(s)) > 512 {
			return out, ErrOptions
		}
		r, e := dom.CompileURLPattern(s)
		if e != nil || r.GroupNumberFromName("location") < 0 {
			return out, ErrOptions
		}
	}
	out = ADPDetailOptions{"https://" + u.Host + prefix, id, cid, cc, loc, config}
	return out, nil
}
func (o ADPDetailOptions) query() string {
	return "cid=" + url.QueryEscape(o.CID) + "&ccId=" + url.QueryEscape(o.CCID) + "&lang=" + url.QueryEscape(o.Locale)
}
func (o ADPDetailOptions) DetailURL() string {
	return o.Base + "/careercenter/public/events/staffing/v1/job-requisitions/" + url.PathEscape(o.ID) + "?" + o.query() + "&locale=" + url.QueryEscape(o.Locale)
}
func (o ADPDetailOptions) DocumentURL() string {
	return o.Base + "/careercenter/public/events/staffing/v1/work-fulfillment/documents/123?" + o.query()
}
func (o ADPDetailOptions) ResourceMatches(s string) bool {
	return s == o.DetailURL() || s == o.DocumentURL()
}
func ADPInlineDescription(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	plain := strings.Join(strings.Fields(html.UnescapeString(adpTag.ReplaceAllString(s, " "))), " ")
	if plain == "" || len([]rune(plain)) <= 200 && adpAttachmentPlaceholder.MatchString(plain) {
		return ""
	}
	return s
}
func ADPAttachmentPath(row map[string]any) string {
	links, _ := row["links"].([]any)
	for _, v := range links {
		m, ok := v.(map[string]any)
		if !ok || !strings.EqualFold(bambooClean(m["targetSchema"]), "docx") {
			continue
		}
		schema, ok := m["schema"].(string)
		args, aok := m["payLoadArguments"].([]any)
		if !ok || !aok {
			continue
		}
		for _, v := range args {
			m, ok := v.(map[string]any)
			if !ok {
				continue
			}
			path, ok := m["argumentPath"].(string)
			if !ok || path == "" {
				continue
			}
			s := strings.TrimRight(path, "/") + "/" + schema
			if len([]rune(s)) > 2048 {
				continue
			}
			safe := true
			for _, r := range s {
				if r < 32 || r == 127 {
					safe = false
				}
			}
			if safe {
				return s
			}
		}
	}
	return ""
}
func (o ADPDetailOptions) DocumentHeaders(path string) http.Header {
	return http.Header{"Filepath": []string{path}, "Isabsolutepath": []string{"true"}, "Isattachmenttype": []string{"true"}, "Locale": []string{o.Locale}, "X-Requested-With": []string{"XMLHttpRequest"}}
}
func ADPDetailFields(d *Document, o ADPDetailOptions, description string) (map[string]any, error) {
	row, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	title, _ := row["requisitionTitle"].(string)
	locs := []string{}
	seen := map[string]bool{}
	rows, _ := row["requisitionLocations"].([]any)
	for _, v := range rows {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		nc, _ := m["nameCode"].(map[string]any)
		s := bambooClean(nc["shortName"])
		s = adpSpaceComma.ReplaceAllString(strings.Join(strings.Fields(s), " "), ",")
		if s != "" && !seen[s] {
			seen[s] = true
			locs = append(locs, s)
		}
	}
	if len(locs) == 0 {
		if pattern, ok := o.Config["title_location_pattern"].(string); ok {
			r, e := dom.CompileURLPattern(pattern)
			if e != nil {
				return nil, e
			}
			match, e := r.FindStringMatch(title)
			if e != nil {
				return nil, e
			}
			if match != nil {
				s := strings.Trim(strings.Join(strings.Fields(match.GroupByName("location").String()), " "), " ,-–—")
				if s != "" {
					locs = append(locs, s)
				}
			}
		}
	}
	var locations, employment, salary, metadata any
	if len(locs) > 0 {
		locations = locs
	}
	w, _ := row["workLevelCode"].(map[string]any)
	if s := ADPEmploymentType(w["shortName"]); s != "" {
		employment = s
	}
	custom, _ := row["customFieldGroup"].(map[string]any)
	if pay, ok := row["payGradeRange"].(map[string]any); ok {
		minimum, _ := pay["minimumRate"].(map[string]any)
		maximum, _ := pay["maximumRate"].(map[string]any)
		if minimum["amountValue"] != nil || maximum["amountValue"] != nil {
			currency := minimum["currencyCode"]
			if !detailTruthy(currency) {
				currency = maximum["currencyCode"]
			}
			var unit any
			codes, _ := custom["codeFields"].([]any)
			for _, v := range codes {
				f, ok := v.(map[string]any)
				if !ok {
					continue
				}
				nc, _ := f["nameCode"].(map[string]any)
				if nc["codeValue"] == "SalaryType" {
					value := f["shortName"]
					if !detailTruthy(value) {
						value = f["codeValue"]
					}
					if s := ADPSalaryUnit(value); s != "" {
						unit = s
					}
					break
				}
			}
			salary = map[string]any{"currency": currency, "min": minimum["amountValue"], "max": maximum["amountValue"], "unit": unit}
		}
	}
	md := map[string]any{}
	for from, to := range map[string]string{"clientRequisitionID": "requisition_id", "itemID": "item_id"} {
		if detailTruthy(row[from]) {
			md[to] = row[from]
		}
	}
	fields, _ := custom["stringFields"].([]any)
	for _, v := range fields {
		f, ok := v.(map[string]any)
		if !ok {
			continue
		}
		nc, _ := f["nameCode"].(map[string]any)
		if !detailTruthy(f["stringValue"]) {
			continue
		}
		switch nc["codeValue"] {
		case "ExternalJobID":
			md["external_job_id"] = f["stringValue"]
		case "JobClass":
			md["job_class"] = f["stringValue"]
		}
	}
	if len(md) > 0 {
		metadata = md
	}
	var t, desc, date any
	if title != "" {
		t = title
	}
	if description != "" {
		desc = description
	}
	if s, ok := row["postDate"].(string); ok && s != "" {
		date = s
	}
	return map[string]any{"title": t, "description": desc, "locations": locations, "employment_type": employment, "job_location_type": nil, "date_posted": date, "base_salary": salary, "language": nil, "extras": nil, "metadata": metadata}, nil
}

//go:embed adp_salary_units.json
var adpSalaryUnitsJSON []byte
var adpSalaryRules struct {
	Units      map[string]string
	Substrings [][2]string
}

func init() {
	if json.Unmarshal(adpSalaryUnitsJSON, &adpSalaryRules) != nil {
		panic("invalid ADP salary units")
	}
}
func ADPSalaryUnit(v any) string {
	s, ok := v.(string)
	if !ok {
		return ""
	}
	key := strings.ToLower(strings.TrimSpace(s))
	if s := adpSalaryRules.Units[key]; s != "" {
		return s
	}
	for _, pair := range adpSalaryRules.Substrings {
		if strings.Contains(key, pair[0]) {
			return pair[1]
		}
	}
	return ""
}

// ADP checks the declared length before buffering details or documents.
func (o ADPDetailOptions) HonorContentLength() bool { return true }
