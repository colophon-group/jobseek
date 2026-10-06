package apisniffer

import (
	"encoding/json"
	"golang.org/x/text/cases"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

type PaycomOptions struct{ Token string }
type PaycomBootstrap struct {
	ServiceURL string
	Headers    http.Header `json:"-"`
}

var paycomToken = regexp.MustCompile(`^[0-9a-f]{32}$`)
var paycomConfig = regexp.MustCompile(`\bvar\s+configsFromHost\s*=\s*`)
var paycomDetailPath = regexp.MustCompile(`(?i)^/v4/ats/web\.php/portal/[0-9a-f]{32}/jobs/([1-9][\p{Nd}]*)/?$`)

func PaycomTokenFromURL(source string) string {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || (strings.ToLower(u.Hostname()) != "paycomonline.net" && strings.ToLower(u.Hostname()) != "www.paycomonline.net") {
		return ""
	}
	p := []string{}
	for _, v := range strings.Split(u.Path, "/") {
		if v != "" {
			p = append(p, v)
		}
	}
	if len(p) < 5 || p[0] != "v4" || p[1] != "ats" || p[2] != "web.php" || p[3] != "portal" {
		return ""
	}
	v := strings.ToLower(strings.TrimSpace(p[4]))
	if !paycomToken.MatchString(v) {
		return ""
	}
	return v
}
func PaycomOptionsFromMetadata(source, raw string) (PaycomOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return PaycomOptions{}, ErrOptions
	}
	for _, k := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return PaycomOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return PaycomOptions{}, ErrOptions
	}
	direct := PaycomTokenFromURL(source)
	configured := ""
	if v, exists := m["token"]; exists {
		s, ok := v.(string)
		if !ok {
			return PaycomOptions{}, ErrOptions
		}
		configured = strings.ToLower(strings.TrimSpace(s))
		if !paycomToken.MatchString(configured) {
			return PaycomOptions{}, ErrOptions
		}
	}
	if direct != "" && configured != "" && direct != configured {
		return PaycomOptions{}, ErrOptions
	}
	if direct == "" {
		direct = configured
	}
	if direct == "" {
		return PaycomOptions{}, ErrOptions
	}
	return PaycomOptions{direct}, nil
}
func (o PaycomOptions) PortalURL() string {
	return "https://www.paycomonline.net/v4/ats/web.php/portal/" + o.Token + "/career-page"
}
func (o PaycomOptions) JobURL(id string) string {
	return "https://www.paycomonline.net/v4/ats/web.php/portal/" + o.Token + "/jobs/" + id
}
func PaycomTrustedService(s string) bool {
	u, e := url.Parse(s)
	if e != nil || !validURL(s) || u.RawQuery != "" || u.RawPath != "" {
		return false
	}
	h := strings.ToLower(u.Hostname())
	return h == "paycomonline.net" || strings.HasSuffix(h, ".paycomonline.net")
}
func (o PaycomOptions) ResourceMatches(s string) bool {
	if s == o.PortalURL() {
		return true
	}
	suffix := "/api/ats/job-posting-previews/search"
	if !strings.HasSuffix(s, suffix) {
		return false
	}
	return PaycomTrustedService(strings.TrimSuffix(s, suffix))
}
func (b PaycomBootstrap) SearchURL() string {
	return b.ServiceURL + "/api/ats/job-posting-previews/search"
}
func (b PaycomBootstrap) DetailURL(id string) string {
	return b.ServiceURL + "/api/ats/job-postings/" + id
}
func (b PaycomBootstrap) ResourceMatches(s string) bool { return s == b.SearchURL() }
func PaycomExtractBootstrap(page string, o PaycomOptions) (PaycomBootstrap, error) {
	b := PaycomBootstrap{}
	m := paycomConfig.FindStringIndex(page)
	if m == nil {
		return b, ErrInventory
	}
	d := json.NewDecoder(strings.NewReader(page[m[1]:]))
	d.UseNumber()
	var config map[string]any
	if d.Decode(&config) != nil || config == nil {
		return b, ErrInventory
	}
	token := bambooClean(config["sessionJWT"])
	lib, ok := config["libConfig"].(string)
	if token == "" || len(strings.Split(token, ".")) != 3 || len(token) > 8192 || strings.ContainsAny(token, "\r\n\x00") || !ok {
		return b, ErrInventory
	}
	doc, e := Decode([]byte(lib))
	if e != nil {
		return b, e
	}
	settings, ok := doc.Value.(map[string]any)
	if !ok {
		return b, ErrInventory
	}
	service, ok := settings["atsPortalMantleServiceUrl"].(string)
	if !ok || !PaycomTrustedService(service) {
		return b, ErrInventory
	}
	b.ServiceURL = strings.TrimRight(service, "/")
	locale := bambooClean(settings["locale"])
	if locale == "" {
		locale = "en-US"
	}
	if len(locale) > 8192 || strings.ContainsAny(locale, "\r\n\x00") {
		return b, ErrInventory
	}
	highlights := "false"
	if detailTruthy(settings["translationHighlights"]) {
		highlights = "true"
	}
	b.Headers = http.Header{"Accept": []string{"application/json"}, "Authorization": []string{token}, "Locale": []string{locale}, "Translation-Highlights": []string{highlights}, "Portal-Host-Referrer": []string{o.PortalURL()}}
	return b, nil
}
func PaycomSearchPayload(skip int) ([]byte, error) {
	if skip < 0 || skip >= 50000 {
		return nil, ErrOptions
	}
	return json.Marshal(map[string]any{"skip": skip, "take": 100, "filtersForQuery": map[string]any{"distanceFrom": 0, "workEnvironments": []any{}, "positionTypes": []any{}, "educationLevels": []any{}, "categories": []any{}, "travelTypes": []any{}, "shiftTypes": []any{}, "otherFilters": []any{}, "keywordSearchText": "", "location": "", "sortOption": ""}})
}
func PaycomPage(d *Document) (int64, []any, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	n, ok := m["jobPostingPreviewsCount"].(json.Number)
	if !ok {
		return 0, nil, ErrInventory
	}
	total, e := n.Int64()
	rows, ok := m["jobPostingPreviews"].([]any)
	if e != nil || total < 0 || !ok {
		return 0, nil, ErrInventory
	}
	return total, rows, nil
}
func paycomJobID(v any) string {
	var raw string
	switch x := v.(type) {
	case string:
		raw = softgardenDecimal(x)
		for _, r := range raw {
			if r < '0' || r > '9' {
				return ""
			}
		}
	case json.Number:
		raw = x.String()
	default:
		return ""
	}
	if len(raw) == 0 || len(raw) > 4300 {
		return ""
	}
	id, ok := new(big.Int).SetString(raw, 10)
	if !ok || id.Sign() <= 0 {
		return ""
	}
	return id.String()
}
func PaycomJobFields(raw any, o PaycomOptions) map[string]any {
	r, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id := paycomJobID(r["jobId"])
	if id == "" {
		return nil
	}
	location := bambooClean(r["locations"])
	var locations any
	if location != "" {
		locations = []string{location}
	}
	md := map[string]any{"job_id": json.Number(id)}
	if r["isHotJob"] != nil && r["isHotJob"] != "" {
		md["is_hot_job"] = r["isHotJob"]
	}
	clean := func(v any) any {
		if s := bambooClean(v); s != "" {
			return s
		}
		return nil
	}
	return map[string]any{"url": o.JobURL(id), "title": clean(r["jobTitle"]), "description": clean(r["description"]), "locations": locations, "employment_type": clean(r["positionType"]), "job_location_type": clean(r["remoteType"]), "date_posted": clean(r["postedOn"]), "language": nil, "metadata": md, "extras": nil}
}
func PaycomDetailRoute(source string) (PaycomOptions, string, error) {
	token := PaycomTokenFromURL(source)
	u, e := url.Parse(source)
	if token == "" || e != nil {
		return PaycomOptions{}, "", ErrOptions
	}
	m := paycomDetailPath.FindStringSubmatch(u.Path)
	if len(m) != 2 {
		return PaycomOptions{}, "", ErrOptions
	}
	return PaycomOptions{token}, m[1], nil
}
func PaycomDefaultLocations(options map[string]any) ([]string, error) {
	v := options["defaults"]
	if v == nil {
		return nil, nil
	}
	m, ok := v.(map[string]any)
	if !ok || len(m) != 1 {
		return nil, ErrOptions
	}
	raw, ok := m["locations"].([]any)
	if !ok || len(raw) == 0 {
		return nil, ErrOptions
	}
	locations := []string{}
	seen := map[string]bool{}
	for _, v := range raw {
		s := bambooClean(v)
		if s == "" {
			return nil, ErrOptions
		}
		if !seen[s] {
			seen[s] = true
			locations = append(locations, s)
		}
	}
	return locations, nil
}
func PaycomDetailFields(d *Document, defaults []string) (map[string]any, string, error) {
	payload, ok := d.Value.(map[string]any)
	if !ok {
		return nil, "", ErrInventory
	}
	row, ok := payload["jobPosting"].(map[string]any)
	if !ok {
		return nil, "", ErrInventory
	}
	google := map[string]any{}
	if s := bambooClean(row["googleJobJson"]); s != "" {
		if doc, e := Decode([]byte(s)); e == nil {
			if m, ok := doc.Value.(map[string]any); ok {
				google = m
			}
		}
	}
	clean := func(v any) any {
		if s := bambooClean(v); s != "" {
			return s
		}
		return nil
	}
	title := clean(row["jobTitle"])
	if title == nil {
		title = clean(google["title"])
	}
	employment := clean(row["positionType"])
	if employment == nil {
		employment = clean(google["employmentType"])
	}
	locations := []string{}
	if v := bambooClean(row["location"]); v != "" {
		locations = append(locations, v)
	}
	secondary, _ := row["secondaryLocations"].([]any)
	fold := cases.Fold()
	for _, v := range secondary {
		s := bambooClean(v)
		if s == "" {
			continue
		}
		seen := false
		for _, old := range locations {
			if fold.String(old) == fold.String(s) {
				seen = true
			}
		}
		if !seen {
			locations = append(locations, s)
		}
	}
	if len(locations) == 0 {
		locations = defaults
	}
	var locs any
	if len(locations) > 0 {
		locs = locations
	}
	md := map[string]any{}
	for from, to := range map[string]string{"jobId": "ats_job_id", "clientCode": "client_code", "jobCategory": "department", "jobShift": "shift", "educationLevel": "education_level", "travelPercentage": "travel_percentage", "isHotJob": "is_hot_job"} {
		if row[from] != nil && row[from] != "" {
			md[to] = row[from]
		}
	}
	var metadata any
	if len(md) > 0 {
		metadata = md
	}
	var extras any
	if q := clean(row["qualifications"]); q != nil {
		extras = map[string]any{"qualifications": q}
	}
	return map[string]any{"title": title, "description": clean(row["description"]), "locations": locs, "employment_type": employment, "job_location_type": clean(row["remoteType"]), "date_posted": clean(google["datePosted"]), "base_salary": nil, "language": nil, "extras": extras, "metadata": metadata}, bambooClean(row["salaryRange"]), nil
}
