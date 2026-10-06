package apisniffer

import (
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const AlmaGraphQLURL = "https://api.capybara.lmc.cz/api/graphql/widget"

type AlmaWidget struct {
	ID         string
	APIKey     string `json:"-"`
	DetailPath string
}
type AlmaOptions struct {
	Host, Country string
	Widget        AlmaWidget
}

var almaAnchor = regexp.MustCompile(`"widgets"\s*:\s*\{\s*"main"\s*:\s*\{`)
var almaWidgetID = regexp.MustCompile(`"id"\s*:\s*"([0-9a-fA-F-]{36})"`)
var almaKey = regexp.MustCompile(`"apiKey"\s*:\s*"([a-fA-F0-9]{32,})"`)
var almaDetail = regexp.MustCompile(`"detailPath"\s*:\s*"([^"]+)"`)
var almaInline = regexp.MustCompile(`window\s*\.\s*__LMC_CAREER_WIDGET__\s*\.\s*push\s*\(\s*`)
var almaUUID = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
var almaAPIKey = regexp.MustCompile(`^[a-fA-F0-9]{32,256}$`)
var almaChunk = regexp.MustCompile(`["'](react\.[A-Za-z0-9._-]+\.react\.min\.js)["']`)

func almaCountry(host string) string {
	host = strings.TrimPrefix(strings.ToLower(host), "www.")
	for suffix, country := range map[string]string{".jobs.cz": "cz", ".topjobs.sk": "sk"} {
		if strings.HasSuffix(host, suffix) {
			slug := strings.TrimSuffix(host, suffix)
			switch slug {
			case "", "www", "api", "cdn", "assets", "static", "app", "help":
				return ""
			}
			return country
		}
	}
	return ""
}
func AlmaOptionsFromMetadata(boardURL, metadata string) (AlmaOptions, error) {
	d, e := Decode([]byte(metadata))
	if e != nil {
		return AlmaOptions{}, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return AlmaOptions{}, ErrOptions
	}
	u, e := url.Parse(boardURL)
	if e != nil || !validURL(strings.Split(boardURL, "#")[0]) {
		return AlmaOptions{}, ErrOptions
	}
	for _, k := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return AlmaOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return AlmaOptions{}, ErrOptions
	}
	host, _ := m["host"].(string)
	host = strings.ToLower(host)
	if host == "" {
		slug, _ := m["slug"].(string)
		country, _ := m["country"].(string)
		suffix := map[string]string{"cz": ".jobs.cz", "sk": ".topjobs.sk"}[country]
		if slug != "" && suffix != "" {
			host = slug + suffix
		}
	}
	if host == "" {
		candidate := strings.TrimPrefix(strings.ToLower(u.Hostname()), "www.")
		if almaCountry(candidate) != "" {
			host = candidate
		}
	}
	origin, e := url.Parse("https://" + host)
	if e != nil || host == "" || len(host) > 253 || origin.Hostname() != host || origin.Port() != "" || origin.User != nil || origin.Path != "" || origin.RawQuery != "" || origin.Fragment != "" || !validURL(origin.String()) {
		return AlmaOptions{}, ErrOptions
	}
	country := almaCountry(host)
	if country == "" {
		country, _ = m["country"].(string)
		if country == "" {
			country = "cz"
		}
	}
	widget := AlmaWidget{}
	widget.ID, _ = m["widget_id"].(string)
	widget.APIKey, _ = m["api_key"].(string)
	widget.DetailPath, _ = m["detail_path"].(string)
	if widget.ID != "" && !almaUUID.MatchString(widget.ID) || widget.APIKey != "" && !almaAPIKey.MatchString(widget.APIKey) {
		return AlmaOptions{}, ErrOptions
	}
	return AlmaOptions{host, country, widget}, nil
}
func (o AlmaOptions) RootURL() string   { return "https://" + o.Host + "/" }
func (o AlmaOptions) ScriptURL() string { return o.RootURL() + "assets/js/script.min.js" }
func (o AlmaOptions) ResourceMatches(raw string) bool {
	if raw == AlmaGraphQLURL || raw == o.RootURL() || raw == o.ScriptURL() || raw == o.RootURL()+"assets/js/react.min.js" {
		return true
	}
	prefix := o.RootURL() + "assets/js/"
	if !strings.HasPrefix(raw, prefix) {
		return false
	}
	file := strings.TrimPrefix(raw, prefix)
	matches := almaChunk.FindStringSubmatch(`"` + file + `"`)
	return len(matches) == 2 && matches[1] == file
}
func ExtractAlmaWidget(source string, inline bool) (AlmaWidget, bool) {
	if !inline {
		at := almaAnchor.FindStringIndex(source)
		if at == nil {
			return AlmaWidget{}, false
		}
		window := []rune(source[at[1]:])
		if len(window) > 65536 {
			window = window[:65536]
		}
		text := string(window)
		id, key := almaWidgetID.FindStringSubmatch(text), almaKey.FindStringSubmatch(text)
		if len(id) != 2 || len(key) != 2 {
			return AlmaWidget{}, false
		}
		path := "detail-pozice"
		if m := almaDetail.FindStringSubmatch(text); len(m) == 2 {
			path = m[1]
		}
		return AlmaWidget{id[1], key[1], path}, true
	}
	for _, at := range almaInline.FindAllStringIndex(source, -1) {
		var raw json.RawMessage
		if json.NewDecoder(strings.NewReader(source[at[1]:])).Decode(&raw) != nil {
			continue
		}
		d, e := Decode(raw)
		if e != nil {
			continue
		}
		m, ok := d.Value.(map[string]any)
		if !ok {
			continue
		}
		id, _ := m["widgetId"].(string)
		key, _ := m["apiKey"].(string)
		if !almaUUID.MatchString(id) || !almaAPIKey.MatchString(key) {
			continue
		}
		path, _ := m["detailPath"].(string)
		if path == "" {
			path = "detail-pozice"
		}
		return AlmaWidget{id, key, path}, true
	}
	return AlmaWidget{}, false
}
func AlmaReactChunks(source string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, m := range almaChunk.FindAllStringSubmatch(source, -1) {
		if !seen[m[1]] {
			out = append(out, m[1])
			seen[m[1]] = true
			if len(out) == 16 {
				break
			}
		}
	}
	return out
}
func AlmaHeaders(key, host string) http.Header {
	return http.Header{"Content-Type": {"application/json"}, "X-Api-Key": {key}, "Origin": {"https://" + host}, "Referer": {"https://" + host + "/"}, "Accept": {"*/*"}}
}
func AlmaFlattenGroups(value any) ([]map[string]any, error) {
	result := []map[string]any{}
	var walk func(any, int) error
	walk = func(value any, depth int) error {
		if !detailTruthy(value) {
			return nil
		}
		group, ok := value.(map[string]any)
		if !ok || depth > 32 {
			return ErrInventory
		}
		if detailTruthy(group["jobAds"]) {
			rows, ok := group["jobAds"].([]any)
			if !ok {
				return ErrInventory
			}
			for _, v := range rows {
				row, ok := v.(map[string]any)
				if !ok {
					return ErrInventory
				}
				result = append(result, row)
			}
		}
		if detailTruthy(group["groups"]) {
			children, ok := group["groups"].([]any)
			if !ok {
				return ErrInventory
			}
			for _, child := range children {
				if e := walk(child, depth+1); e != nil {
					return e
				}
			}
		}
		return nil
	}
	if e := walk(value, 0); e != nil {
		return nil, e
	}
	return result, nil
}

type AlmaJob struct {
	Job
	Language   any            `json:"language"`
	BaseSalary map[string]any `json:"base_salary"`
}

func AlmaProject(d *Document, raw map[string]any, host, path, country string) (*AlmaJob, error) {
	if !detailTruthy(raw["id"]) || !detailTruthy(raw["title"]) {
		return nil, nil
	}
	id, e := d.String(raw["id"])
	if e != nil {
		return nil, e
	}
	j := Job{URL: "https://" + host + "/" + path + "?r=detail&id=" + id, Title: raw["title"], Description: raw["teaser"], Metadata: map[string]any{"id": id, "country": country}}
	seen := map[string]bool{}
	if detailTruthy(raw["locations"]) {
		rows, ok := raw["locations"].([]any)
		if !ok {
			return nil, ErrInventory
		}
		for _, v := range rows {
			if !detailTruthy(v) {
				continue
			}
			row, ok := v.(map[string]any)
			if !ok {
				return nil, ErrInventory
			}
			parts := []string{}
			taken := map[string]bool{}
			for _, k := range []string{"cityPart", "city", "district", "region", "country"} {
				if detailTruthy(row[k]) {
					s, ok := row[k].(string)
					if !ok {
						return nil, ErrInventory
					}
					if !taken[s] {
						parts = append(parts, s)
						taken[s] = true
					}
				}
			}
			label := strings.Join(parts, ", ")
			if label != "" && !seen[label] {
				j.Locations = append(j.Locations, label)
				seen[label] = true
			}
		}
	}
	if v, ok := raw["validFrom"].(string); ok && len([]rune(v)) >= 10 {
		j.DatePosted = string([]rune(v)[:10])
	}
	if params, ok := raw["parameters"].(map[string]any); ok {
		rows, _ := params["employmentTypesObjects"].([]any)
		mapping := map[string]string{"201300001": "full-time", "201300002": "part-time", "201300003": "contract", "201300004": "contract", "201300005": "internship", "201300006": "contract", "201300007": "part-time"}
		for _, v := range rows {
			row, ok := v.(map[string]any)
			if !ok {
				return nil, ErrInventory
			}
			id, _ := row["id"].(string)
			if mapped := mapping[id]; mapped != "" {
				j.EmploymentType = mapped
				break
			}
			if detailTruthy(row["label"]) {
				j.EmploymentType = row["label"]
				break
			}
		}
	}
	if employer, ok := raw["employer"].(map[string]any); ok && detailTruthy(employer["companyName"]) {
		j.Metadata["company_name"] = employer["companyName"]
	}
	for out, key := range map[string]string{"fields": "fieldsObjects", "professions": "professionsObjects"} {
		if !detailTruthy(raw[key]) {
			continue
		}
		rows, ok := raw[key].([]any)
		if !ok {
			return nil, ErrInventory
		}
		labels := []any{}
		for _, v := range rows {
			row, ok := v.(map[string]any)
			if !ok {
				return nil, ErrInventory
			}
			if detailTruthy(row["label"]) {
				labels = append(labels, row["label"])
			}
		}
		if len(labels) > 0 {
			j.Metadata[out] = labels
		}
	}
	var salary map[string]any
	if rawSalary, ok := raw["salary"].(map[string]any); ok && detailTruthy(rawSalary["currency"]) && (rawSalary["min"] != nil || rawSalary["max"] != nil) {
		unit := "month"
		if period, ok := rawSalary["period"].(string); ok {
			if v := map[string]string{"měsíc": "month", "mesiac": "month", "hodina": "hour", "rok": "year", "month": "month", "hour": "hour", "year": "year"}[strings.ToLower(period)]; v != "" {
				unit = v
			}
		}
		salary = map[string]any{"currency": rawSalary["currency"], "unit": unit, "min": nil, "max": nil}
		for _, k := range []string{"min", "max"} {
			switch v := rawSalary[k].(type) {
			case json.Number:
				if n, e := v.Float64(); e == nil {
					salary[k] = n
				}
			case string:
				if n, e := strconv.ParseFloat(strings.TrimSpace(v), 64); e == nil {
					salary[k] = n
				}
			case bool:
				n := 0.0
				if v {
					n = 1
				}
				salary[k] = n
			}
		}
	}
	return &AlmaJob{j, raw["languageIso"], salary}, nil
}
