package apisniffer

import (
	"encoding/json"
	"errors"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type CornerstoneBoard struct {
	Tenant, Corp, Domain string
	SiteID               int64
}
type CornerstoneContext struct {
	APIBase, CultureName string
	CultureID            int64
	Headers              http.Header `json:"-"`
}

var ErrCornerstoneContextMissing = errors.New("Cornerstone public context missing")
var cornerstoneTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var cornerstoneCorp = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$`)
var cornerstoneCulture = regexp.MustCompile(`^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$`)
var cornerstoneMarker = regexp.MustCompile(`\bcsod\.context\s*=\s*`)

func cornerstoneBoard(tenant, site, corp, domain any) (CornerstoneBoard, error) {
	t := strings.ToLower(bambooClean(tenant))
	c := strings.ToLower(bambooClean(corp))
	d := strings.TrimRight(strings.ToLower(bambooClean(domain)), ".")
	switch t {
	case "api", "app", "help", "login", "portal", "support", "www":
		return CornerstoneBoard{}, ErrOptions
	}
	n := int64(0)
	switch v := site.(type) {
	case json.Number:
		n, _ = v.Int64()
	case string:
		n, _ = strconv.ParseInt(v, 10, 64)
	}
	if !cornerstoneTenant.MatchString(t) || !cornerstoneCorp.MatchString(c) || (d != "csod.com" && d != "csodfed.com") || n < 1 || n > 2147483647 {
		return CornerstoneBoard{}, ErrOptions
	}
	return CornerstoneBoard{t, c, d, n}, nil
}
func CornerstoneBoardFromURL(source string) (CornerstoneBoard, error) {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) {
		return CornerstoneBoard{}, ErrOptions
	}
	host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	domain := "csod.com"
	if strings.HasSuffix(host, ".csodfed.com") {
		domain = "csodfed.com"
	}
	if !strings.HasSuffix(host, "."+domain) {
		return CornerstoneBoard{}, ErrOptions
	}
	parts := []string{}
	for _, p := range strings.Split(u.Path, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) != 5 && len(parts) != 7 {
		return CornerstoneBoard{}, ErrOptions
	}
	if strings.ToLower(parts[0]) != "ux" || strings.ToLower(parts[1]) != "ats" || strings.ToLower(parts[2]) != "careersite" || strings.ToLower(parts[4]) != "home" {
		return CornerstoneBoard{}, ErrOptions
	}
	if len(parts) == 7 && (strings.ToLower(parts[5]) != "requisition" || cornerstoneID(parts[6]) == "") {
		return CornerstoneBoard{}, ErrOptions
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) != 1 || len(q["c"]) != 1 {
		return CornerstoneBoard{}, ErrOptions
	}
	return cornerstoneBoard(strings.TrimSuffix(host, "."+domain), parts[3], q.Get("c"), domain)
}
func CornerstoneOptionsFromMetadata(source, raw string) (CornerstoneBoard, error) {
	m, e := fifthDirectMetadata(source, raw)
	if e != nil {
		return CornerstoneBoard{}, e
	}
	domain := m["domain"]
	if domain == nil {
		domain = "csod.com"
	}
	if b, e := cornerstoneBoard(m["tenant"], m["site_id"], m["corp"], domain); e == nil {
		return b, nil
	}
	return CornerstoneBoardFromURL(source)
}
func (b CornerstoneBoard) ListingURL() string {
	return "https://" + b.Tenant + "." + b.Domain + "/ux/ats/careersite/" + strconv.FormatInt(b.SiteID, 10) + "/home?c=" + url.QueryEscape(b.Corp)
}
func (b CornerstoneBoard) JobURL(id string) string {
	root, q, _ := strings.Cut(b.ListingURL(), "?")
	return root + "/requisition/" + id + "?" + q
}
func (b CornerstoneBoard) ResourceMatches(source string) bool {
	if source == b.ListingURL() {
		return true
	}
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || u.Path != "/rec-job-search/external/jobs" || u.RawQuery != "" {
		return false
	}
	h := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	return h == "api."+b.Domain || strings.HasSuffix(h, ".api."+b.Domain)
}
func CornerstoneExtractContext(page string, b CornerstoneBoard) (CornerstoneContext, error) {
	out := CornerstoneContext{}
	m := cornerstoneMarker.FindStringIndex(page)
	if m == nil {
		return out, ErrCornerstoneContextMissing
	}
	dec := json.NewDecoder(strings.NewReader(page[m[1]:]))
	dec.UseNumber()
	var raw map[string]any
	if dec.Decode(&raw) != nil || raw == nil {
		return out, ErrInventory
	}
	corp := strings.ToLower(bambooClean(raw["corp"]))
	token, ok := raw["token"].(string)
	n, nok := raw["cultureID"].(json.Number)
	culture, cok := raw["cultureName"].(string)
	id, e := n.Int64()
	if corp != b.Corp || !ok || len(token) < 64 || len(token) > 20000 || len(strings.Split(token, ".")) != 3 || strings.ContainsAny(token, "\r\n\x00") || !nok || e != nil || id <= 0 || !cok || !cornerstoneCulture.MatchString(culture) {
		return out, ErrInventory
	}
	endpoints, _ := raw["endpoints"].(map[string]any)
	base, ok := endpoints["cloud"].(string)
	u, e := url.Parse(base)
	if !ok || e != nil || !validURL(base) || (u.Path != "" && u.Path != "/") || u.RawQuery != "" {
		return out, ErrInventory
	}
	h := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	if h != "api."+b.Domain && !strings.HasSuffix(h, ".api."+b.Domain) {
		return out, ErrInventory
	}
	out.APIBase = "https://" + h + "/"
	out.CultureID = id
	out.CultureName = culture
	out.Headers = http.Header{"Accept": []string{"application/json"}, "Authorization": []string{"Bearer " + token}, "Csod-Accept-Language": []string{culture}}
	return out, nil
}
func (c CornerstoneContext) SearchURL() string                  { return c.APIBase + "rec-job-search/external/jobs" }
func (c CornerstoneContext) ResourceMatches(source string) bool { return source == c.SearchURL() }
func CornerstoneSearchPayload(b CornerstoneBoard, c CornerstoneContext, page int) ([]byte, error) {
	if page < 1 || page > 500 {
		return nil, ErrOptions
	}
	return json.Marshal(map[string]any{"careerSiteId": b.SiteID, "careerSitePageId": b.SiteID, "pageNumber": page, "pageSize": 100, "cultureId": c.CultureID, "searchText": "", "cultureName": c.CultureName, "states": []any{}, "countryCodes": []any{}, "cities": []any{}, "placeID": "", "radius": nil, "postingsWithinDays": nil, "customFieldCheckboxKeys": []any{}, "customFieldDropdowns": []any{}, "customFieldRadios": []any{}})
}
func CornerstonePage(d *Document) (int64, []any, error) {
	m, ok := d.Value.(map[string]any)
	if !ok || m["status"] != "Success" {
		return 0, nil, ErrInventory
	}
	data, ok := m["data"].(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	n, ok := data["totalCount"].(json.Number)
	if !ok {
		return 0, nil, ErrInventory
	}
	total, e := n.Int64()
	rows, ok := data["requisitions"].([]any)
	if e != nil || total < 0 || !ok {
		return 0, nil, ErrInventory
	}
	return total, rows, nil
}
func cornerstoneID(v any) string {
	var s string
	switch x := v.(type) {
	case string:
		s = softgardenDecimal(x)
	case json.Number:
		s = x.String()
	default:
		return ""
	}
	if len(s) < 1 || len(s) > 20 {
		return ""
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return ""
		}
	}
	n, ok := new(big.Int).SetString(s, 10)
	if !ok || n.Sign() <= 0 {
		return ""
	}
	return n.String()
}
func CornerstoneJobFields(raw any, b CornerstoneBoard, culture string) map[string]any {
	m, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id := cornerstoneID(m["requisitionId"])
	title := cornerstoneText(m["displayJobTitle"])
	if id == "" || title == nil {
		return nil
	}
	locs := []string{}
	seen := map[string]bool{}
	if rows, ok := m["locations"].([]any); ok {
		for _, v := range rows {
			r, ok := v.(map[string]any)
			if !ok {
				continue
			}
			p := []string{}
			for _, k := range []string{"city", "state", "country"} {
				if s := bambooClean(r[k]); s != "" {
					p = append(p, s)
				}
			}
			s := strings.Join(p, ", ")
			if s != "" && !seen[s] {
				seen[s] = true
				locs = append(locs, s)
			}
		}
	}
	var locations, date, language any
	if len(locs) > 0 {
		locations = locs
	}
	rawDate := bambooClean(m["postingEffectiveDate"])
	formats := []string{"2006-1-2", "2/1/2006", "2-1-2006"}
	if strings.EqualFold(culture, "en-us") {
		formats = []string{"2006-1-2", "1/2/2006", "1-2-2006"}
	}
	for _, f := range formats {
		if value, e := time.Parse(f, rawDate); e == nil {
			date = value.Format("2006-01-02")
			break
		}
	}
	lang := strings.ToLower(strings.SplitN(culture, "-", 2)[0])
	if len(lang) == 2 {
		language = lang
	}
	return map[string]any{"url": b.JobURL(id), "title": title, "description": cornerstoneText(m["externalDescription"]), "locations": locations, "date_posted": date, "language": language, "metadata": map[string]any{"requisition_id": json.Number(id)}, "employment_type": nil, "job_location_type": nil, "extras": nil}
}

func cornerstoneText(v any) any {
	if s := bambooClean(v); s != "" {
		return s
	}
	return nil
}
