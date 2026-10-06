package apisniffer

import (
	"encoding/json"
	"html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/andybalholm/cascadia"
	parser "golang.org/x/net/html"
	"golang.org/x/text/cases"
)

const DayforcePageSize = 25

type DayforceBoard struct{ Tenant, Portal string }
type DayforceSite struct {
	JobBoardID int64
	Culture    string
	Cultures   []string
	Disabled   bool
}

var dayforceTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var dayforcePortal = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?$`)
var dayforceCulture = regexp.MustCompile(`^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$`)
var dayforceHTML = regexp.MustCompile(`<[A-Za-z/]`)
var dayforceParagraphs = regexp.MustCompile(`\n[\t\r\n\v\f \x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]*\n+`)

func dayforceBoard(tenant, portal any) (DayforceBoard, error) {
	t, p := strings.ToLower(bambooClean(tenant)), bambooClean(portal)
	switch t {
	case "api", "app", "help", "support", "www":
		return DayforceBoard{}, ErrOptions
	}
	if !dayforceTenant.MatchString(t) || !dayforcePortal.MatchString(p) {
		return DayforceBoard{}, ErrOptions
	}
	return DayforceBoard{t, p}, nil
}
func DayforceBoardFromURL(source string) (DayforceBoard, error) {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || strings.TrimRight(strings.ToLower(u.Hostname()), ".") != "jobs.dayforcehcm.com" || u.RawQuery != "" || u.RawPath != "" || !strings.HasPrefix(u.Path, "/") {
		return DayforceBoard{}, ErrOptions
	}
	parts := strings.Split(strings.TrimSuffix(strings.TrimPrefix(u.Path, "/"), "/"), "/")
	if len(parts) == 3 && validDayforceCulture(parts[0]) {
		parts = parts[1:]
	} else if len(parts) == 5 && validDayforceCulture(parts[0]) && strings.EqualFold(parts[3], "jobs") && dayforcePositiveID(parts[4]) != 0 {
		parts = parts[1:3]
	}
	if len(parts) != 2 {
		return DayforceBoard{}, ErrOptions
	}
	return dayforceBoard(parts[0], parts[1])
}
func validDayforceCulture(s string) bool { return len(s) <= 32 && dayforceCulture.MatchString(s) }
func DayforceOptionsFromMetadata(source, raw string) (DayforceBoard, int, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return DayforceBoard{}, 0, ErrOptions
	}
	overlap := 0
	if v, exists := m["offset_overlap"]; exists {
		n, ok := v.(json.Number)
		value, err := n.Int64()
		if !ok || err != nil || value < 0 || value >= DayforcePageSize {
			return DayforceBoard{}, 0, ErrOptions
		}
		overlap = int(value)
	}
	b, e := dayforceBoard(m["tenant"], m["portal"])
	if e != nil {
		b, e = DayforceBoardFromURL(source)
	}
	return b, overlap, e
}
func (b DayforceBoard) ListingURL() string {
	return "https://jobs.dayforcehcm.com/" + b.Tenant + "/" + b.Portal
}
func (b DayforceBoard) SearchURL() string {
	return "https://jobs.dayforcehcm.com/api/geo/" + b.Tenant + "/jobposting/search"
}
func (b DayforceBoard) JobURL(culture string, id int64) string {
	return "https://jobs.dayforcehcm.com/" + culture + "/" + b.Tenant + "/" + b.Portal + "/jobs/" + strconv.FormatInt(id, 10)
}
func (b DayforceBoard) ResourceMatches(source string) bool {
	if source == b.SearchURL() {
		return true
	}
	other, e := DayforceBoardFromURL(source)
	if e != nil || other != b {
		return false
	}
	u, _ := url.Parse(source)
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	return len(parts) == 2 || len(parts) == 3
}
func (b DayforceBoard) LocalizedRedirect(source, location string) (string, error) {
	base, e := url.Parse(source)
	target, err := url.Parse(location)
	if e != nil || err != nil || location == "" {
		return "", ErrOptions
	}
	u := base.ResolveReference(target)
	other, e := DayforceBoardFromURL(u.String())
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if e != nil || other != b || len(parts) != 3 || !validDayforceCulture(parts[0]) {
		return "", ErrOptions
	}
	return u.String(), nil
}
func dayforcePositiveID(value any) int64 {
	var s string
	switch v := value.(type) {
	case json.Number:
		s = string(v)
	case string:
		s = softgardenDecimal(v)
	default:
		return 0
	}
	if s == "" {
		return 0
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0
		}
	}
	n, e := strconv.ParseInt(s, 10, 64)
	if e != nil || n < 1 {
		return 0
	}
	return n
}
func DayforceExtractSite(page string, b DayforceBoard) (DayforceSite, error) {
	out := DayforceSite{}
	if len(page) >= 1_000_000 {
		return out, ErrInventory
	}
	tree, e := parser.Parse(strings.NewReader(page))
	if e != nil {
		return out, ErrInventory
	}
	nodes := cascadia.QueryAll(tree, cascadia.MustCompile("script#__NEXT_DATA__"))
	if len(nodes) != 1 {
		return out, ErrInventory
	}
	var text strings.Builder
	for n := nodes[0].FirstChild; n != nil; n = n.NextSibling {
		if n.Type == parser.TextNode {
			text.WriteString(n.Data)
		}
	}
	d, e := Decode([]byte(text.String()))
	if e != nil {
		return out, ErrInventory
	}
	m, _ := d.Value.(map[string]any)
	query, _ := m["query"].(map[string]any)
	identity, e := dayforceBoard(query["clientNamespace"], query["careerSiteXRefCode"])
	if e != nil || identity.Tenant != b.Tenant || !strings.EqualFold(identity.Portal, b.Portal) {
		return out, ErrInventory
	}
	props, _ := m["props"].(map[string]any)
	pageProps, _ := props["pageProps"].(map[string]any)
	state, _ := pageProps["dehydratedState"].(map[string]any)
	queries, ok := state["queries"].([]any)
	if !ok {
		return out, ErrInventory
	}
	var matching []map[string]any
	for _, raw := range queries {
		entry, _ := raw.(map[string]any)
		key, _ := entry["queryKey"].([]any)
		state, _ := entry["state"].(map[string]any)
		site, ok := state["data"].(map[string]any)
		if len(key) == 0 || key[0] != "site-info" || !ok {
			continue
		}
		identity, e := dayforceBoard(site["clientNamespace"], site["jobBoardCode"])
		if e == nil && identity.Tenant == b.Tenant && strings.EqualFold(identity.Portal, b.Portal) {
			matching = append(matching, site)
		}
	}
	if len(matching) != 1 {
		return out, ErrInventory
	}
	site := matching[0]
	out.JobBoardID = dayforcePositiveID(site["jobBoardId"])
	out.Culture = bambooClean(site["cultureCode"])
	cultures, ok := site["isoCultureCodes"].([]any)
	if out.JobBoardID == 0 || !validDayforceCulture(out.Culture) || !ok || len(cultures) == 0 {
		return DayforceSite{}, ErrInventory
	}
	seen := map[string]bool{}
	for _, v := range cultures {
		culture := bambooClean(v)
		folded := cases.Fold().String(culture)
		if !validDayforceCulture(culture) || seen[folded] {
			return DayforceSite{}, ErrInventory
		}
		seen[folded] = true
		out.Cultures = append(out.Cultures, culture)
	}
	if !seen[cases.Fold().String(out.Culture)] {
		return DayforceSite{}, ErrInventory
	}
	if v := site["isDisabled"]; v != nil {
		disabled, ok := v.(bool)
		if !ok {
			return DayforceSite{}, ErrInventory
		}
		out.Disabled = disabled
	}
	return out, nil
}
func DayforceSearchBody(b DayforceBoard, site DayforceSite, offset int) ([]byte, error) {
	if offset < 0 || offset >= 50000 || !validDayforceCulture(site.Culture) {
		return nil, ErrOptions
	}
	return json.Marshal(map[string]any{"clientNamespace": b.Tenant, "jobBoardCode": b.Portal, "cultureCode": site.Culture, "distanceUnit": 0, "paginationStart": offset})
}
func DayforcePage(d *Document, b DayforceBoard, site DayforceSite, offset int) (int64, []any, error) {
	if d == nil {
		return 0, nil, ErrInventory
	}
	m, _ := d.Value.(map[string]any)
	n, ok := m["maxCount"].(json.Number)
	total, e := n.Int64()
	start, sok := m["offset"].(json.Number)
	actual, se := start.Int64()
	count, cok := m["count"].(json.Number)
	size, ce := count.Int64()
	rows, rok := m["jobPostings"].([]any)
	if !ok || e != nil || total < 0 || !sok || se != nil || actual != int64(offset) || !cok || ce != nil || !rok || size != int64(len(rows)) || len(rows) > DayforcePageSize || len(rows) > 0 && int64(offset+len(rows)) > total {
		return 0, nil, ErrInventory
	}
	for _, row := range rows {
		if r, ok := row.(map[string]any); ok {
			if dayforcePositiveID(r["jobBoardId"]) != site.JobBoardID || !strings.EqualFold(bambooClean(r["clientNamespace"]), b.Tenant) {
				return 0, nil, ErrInventory
			}
		}
	}
	return total, rows, nil
}

// The shared rich worker applies the existing description normalizer after
// this provider projection, including the legacy plain-text paragraph rendering.
func DayforceJobFields(raw any, b DayforceBoard, site DayforceSite) map[string]any {
	r, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id := dayforcePositiveID(r["jobPostingId"])
	title := bambooClean(r["jobTitle"])
	if id == 0 || title == "" {
		return nil
	}
	metadata := map[string]any{"job_posting_id": id}
	if req := dayforcePositiveID(r["jobReqId"]); req != 0 {
		metadata["job_req_id"] = req
	}
	if value, ok := r["isEvergreen"].(bool); ok {
		metadata["evergreen"] = value
	}
	if expiry := bambooClean(r["postingExpiryTimestampUTC"]); expiry != "" {
		metadata["expires_at"] = expiry
	}
	var description any
	text := bambooClean(r["jobDescription"])
	if text != "" {
		if !dayforceHTML.MatchString(text) {
			text = strings.ReplaceAll(strings.ReplaceAll(html.UnescapeString(text), "\r\n", "\n"), "\r", "\n")
			paragraphs := []string{}
			for _, part := range dayforceParagraphs.Split(text, -1) {
				lines := []string{}
				for _, line := range strings.Split(part, "\n") {
					if line = inlineTrim(line); line != "" {
						lines = append(lines, strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(line))
					}
				}
				if len(lines) > 0 {
					paragraphs = append(paragraphs, "<p>"+strings.Join(lines, "<br>\n")+"</p>")
				}
			}
			text = strings.Join(paragraphs, "\n")
		}
		description = text
	}
	locations := []string{}
	seen := map[string]bool{}
	var locationType any
	if r["hasVirtualLocation"] == true {
		locations = append(locations, "Virtual")
		seen["virtual"] = true
		locationType = "remote"
	}
	if rows, ok := r["postingLocations"].([]any); ok {
		for _, raw := range rows {
			item, _ := raw.(map[string]any)
			s := bambooClean(item["formattedAddress"])
			key := cases.Fold().String(s)
			if s != "" && !seen[key] {
				seen[key] = true
				locations = append(locations, s)
			}
		}
	}
	var locs, date, language any
	if len(locations) > 0 {
		locs = locations
	}
	rawDate := bambooClean(r["postingStartTimestampUTC"])
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02"} {
		if t, e := time.Parse(layout, rawDate); e == nil {
			date = t.Format("2006-01-02")
			break
		}
	}
	lang := strings.ToLower(strings.SplitN(site.Culture, "-", 2)[0])
	if len(lang) == 2 {
		language = lang
	}
	return map[string]any{"url": b.JobURL(site.Culture, id), "title": title, "description": description, "locations": locs, "job_location_type": locationType, "date_posted": date, "language": language, "metadata": metadata}
}
