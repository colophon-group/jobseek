package apisniffer

import (
	"encoding/json"
	"html"
	"math/big"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/text/cases"
)

type BeisenBoard struct {
	Tenant, Variant, PortalID, TenantID, ListingPath, LegacyTemplate string
}

var beisenTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var beisenPublicID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var beisenCategory = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)
var beisenLegacyID = regexp.MustCompile(`^[1-9][0-9]{0,18}$`)
var BeisenLegacyMarker = regexp.MustCompile(`(?i)\b_splash\([^)]*['"]new_zhiye_com['"]`)
var beisenLinkedListing = regexp.MustCompile(`(?i)href=['"](/(?:Social|social|index))/?['"]`)

func normalizeBeisenTenant(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if !beisenTenant.MatchString(value) || value == "api" || value == "help" || value == "static" || value == "support" || value == "www" {
		return ""
	}
	return value
}

func normalizeBeisenPublicID(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	s = strings.ToLower(strings.TrimSpace(s))
	if !beisenPublicID.MatchString(s) {
		return ""
	}
	return s
}

func positiveBeisenInteger(value any) string {
	n, ok := value.(json.Number)
	if !ok {
		return ""
	}
	x, ok := new(big.Int).SetString(string(n), 10)
	if !ok || x.Sign() <= 0 {
		return ""
	}
	return x.String()
}

// Metadata identity is optional in the legacy monitor. An incomplete optional
// identity falls back to live bootstrap, while a complete mismatched tenant
// fails before origin contact. The queue binds the original metadata unchanged.
func BeisenOptionsFromMetadata(boardURL, metadata string) (BeisenBoard, error) {
	u, err := url.Parse(boardURL)
	if err != nil || len(boardURL) > 4096 || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Port() != "" && u.Port() != "443" || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".zhiye.com") {
		return BeisenBoard{}, ErrOptions
	}
	pairs, err := url.ParseQuery(u.RawQuery)
	count := 0
	for _, v := range pairs {
		count += len(v)
	}
	if err != nil || count > 16 {
		return BeisenBoard{}, ErrOptions
	}
	tenant := normalizeBeisenTenant(strings.TrimSuffix(strings.ToLower(u.Hostname()), ".zhiye.com"))
	if tenant == "" {
		return BeisenBoard{}, ErrOptions
	}
	d, err := Decode([]byte(metadata))
	if err != nil {
		return BeisenBoard{}, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return BeisenBoard{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return BeisenBoard{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return BeisenBoard{}, ErrOptions
	}
	configured := BeisenBoard{}
	if raw, ok := m["tenant"].(string); ok {
		configured.Tenant = normalizeBeisenTenant(raw)
	}
	configured.Variant, _ = m["variant"].(string)
	valid := configured.Tenant != ""
	switch configured.Variant {
	case "modern":
		configured.PortalID = normalizeBeisenPublicID(m["portal_id"])
		configured.TenantID = positiveBeisenInteger(m["tenant_id"])
		valid = valid && configured.PortalID != "" && configured.TenantID != ""
	case "legacy":
		raw, _ := m["listing_path"].(string)
		path := "/" + strings.Trim(strings.TrimSpace(raw), "/")
		switch strings.ToLower(path) {
		case "/social":
			configured.ListingPath = "/Social"
		case "/index":
			configured.ListingPath = "/index"
		}
		configured.LegacyTemplate, _ = m["legacy_template"].(string)
		valid = valid && configured.ListingPath != "" && (configured.LegacyTemplate == "standard" || configured.LegacyTemplate == "inline") && (configured.ListingPath == "/index") == (configured.LegacyTemplate == "inline")
	default:
		valid = false
	}
	if valid {
		if configured.Tenant != tenant {
			return BeisenBoard{}, ErrOptions
		}
		return configured, nil
	}
	return BeisenBoard{Tenant: tenant}, nil
}

func (b BeisenBoard) RootURL() string { return "https://" + b.Tenant + ".zhiye.com/" }
func (b BeisenBoard) APIURL() string  { return b.RootURL() + "api/Jobad/GetJobAdPageList" }
func (b BeisenBoard) ListingURL() string {
	path := b.ListingPath
	if path == "" {
		path = "/Social"
	}
	return strings.TrimSuffix(b.RootURL(), "/") + path
}
func (b BeisenBoard) ModernJobURL(id, category string) (string, error) {
	id = normalizeBeisenPublicID(id)
	category = strings.TrimSpace(category)
	if id == "" || !beisenCategory.MatchString(category) {
		return "", ErrInventory
	}
	route := map[string]string{"1": "social", "2": "campus", "3": "intern"}[category]
	if route == "" {
		route = category
	}
	return b.RootURL() + route + "/detail?jobAdId=" + id, nil
}
func (b BeisenBoard) LegacyJobURL(id string) string {
	if b.LegacyTemplate == "inline" {
		return b.RootURL() + "zwxq?jobId=" + id
	}
	return b.RootURL() + "zpdetail/" + id
}

// A live root bootstrap wins over URL inference. A configured variant and its
// portal/tenant identifiers must still match the live source exactly.
func ResolveBeisenBoard(page, boardURL string, configured BeisenBoard) (BeisenBoard, bool, error) {
	const marker = "var BSGlobal = "
	if at := strings.Index(page, marker); at >= 0 {
		var raw json.RawMessage
		if json.NewDecoder(strings.NewReader(page[at+len(marker):])).Decode(&raw) != nil {
			return BeisenBoard{}, false, ErrInventory
		}
		d, err := Decode(raw)
		if err != nil {
			return BeisenBoard{}, false, ErrInventory
		}
		m, ok := d.Value.(map[string]any)
		if !ok {
			return BeisenBoard{}, false, ErrInventory
		}
		portal := normalizeBeisenPublicID(m["PortalId"])
		info, ok := m["tenantInfo"].(map[string]any)
		if !ok || portal == "" {
			return BeisenBoard{}, false, ErrInventory
		}
		id := positiveBeisenInteger(info["Id"])
		status, ok := info["Status"].(json.Number)
		state, valid := new(big.Int).SetString(string(status), 10)
		if !ok || !valid || id == "" {
			return BeisenBoard{}, false, ErrInventory
		}
		live := BeisenBoard{Tenant: configured.Tenant, Variant: "modern", PortalID: portal, TenantID: id}
		if state.Cmp(big.NewInt(1)) != 0 {
			return live, true, nil
		}
		if configured.Variant != "" && (configured.Variant != "modern" || configured.PortalID != portal || configured.TenantID != id) {
			return BeisenBoard{}, false, ErrInventory
		}
		return live, false, nil
	}
	if configured.Variant == "legacy" {
		return configured, false, nil
	}
	u, err := url.Parse(boardURL)
	if err != nil {
		return BeisenBoard{}, false, ErrInventory
	}
	path := strings.TrimRight(u.Path, "/")
	if strings.ToLower(path) != "/social" && strings.ToLower(path) != "/index" {
		m := beisenLinkedListing.FindStringSubmatch(page)
		if !BeisenLegacyMarker.MatchString(page) || len(m) != 2 {
			return BeisenBoard{}, false, ErrInventory
		}
		path = m[1]
	}
	configured.Variant, configured.ListingPath, configured.LegacyTemplate = "legacy", "/Social", "standard"
	if strings.ToLower(path) == "/index" {
		configured.ListingPath, configured.LegacyTemplate = "/index", "inline"
	}
	return configured, false, nil
}

func (b BeisenBoard) ResourceMatches(resource string) bool {
	if resource == b.RootURL() || resource == b.APIURL() {
		return true
	}
	for _, path := range []string{"/Social", "/index"} {
		// Without a modern bootstrap Python can infer either legacy listing
		// from the board URL/root link, including optional modern metadata.
		base := strings.TrimSuffix(b.RootURL(), "/") + path
		if resource == base {
			return true
		}
		if strings.HasPrefix(resource, base+"?PageIndex=") {
			raw := strings.TrimPrefix(resource, base+"?PageIndex=")
			n, err := strconv.Atoi(raw)
			if err == nil && n >= 2 && n <= 50000 && strconv.Itoa(n) == raw {
				return true
			}
		}
	}
	return false
}

func cleanBeisenString(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	return strings.Join(strings.Fields(html.UnescapeString(s)), " ")
}
func safeBeisenDate(value any) any {
	s := cleanBeisenString(value)
	if strings.HasPrefix(s, "0001-") {
		return nil
	}
	runes := []rune(s)
	if len(runes) > 10 {
		s = string(runes[:10])
	}
	for _, layout := range []string{"2006-01-02", "20060102"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.Format("2006-01-02")
		}
	}
	return nil
}

func BeisenModernJobs(d *Document, b BeisenBoard) ([]Job, int, error) {
	fail := func() ([]Job, int, error) { return nil, 0, ErrInventory }
	m, ok := d.Value.(map[string]any)
	if !ok {
		return fail()
	}
	code, ok := m["Code"].(json.Number)
	f, err := code.Float64()
	if !ok || err != nil || f != 200 {
		return fail()
	}
	rows, ok := m["Data"].([]any)
	if !ok {
		return fail()
	}
	count, ok := m["Count"].(json.Number)
	n, err := strconv.Atoi(string(count))
	if !ok || err != nil || n < 0 {
		return fail()
	}
	jobs := []Job{}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if status := row["Status"]; status != nil {
			switch v := status.(type) {
			case bool:
				if !v {
					continue
				}
			case json.Number:
				f, e := v.Float64()
				if e != nil || f != 1 {
					continue
				}
			case string:
				continue
			default:
				return fail()
			}
		}
		id := normalizeBeisenPublicID(row["Id"])
		category := cleanBeisenString(row["CategoryId"])
		title := cleanBeisenString(row["JobAdName"])
		if id == "" || category == "" || title == "" {
			continue
		}
		link, err := b.ModernJobURL(id, category)
		if err != nil {
			return fail()
		}
		job := Job{URL: link, Title: title, Metadata: map[string]any{}, DatePosted: safeBeisenDate(row["PostDate"])}
		seen := map[string]bool{}
		if locations, ok := row["LocNames"].([]any); ok {
			for _, v := range locations {
				location := cleanBeisenString(v)
				key := cases.Fold().String(location)
				if location != "" && !seen[key] {
					seen[key] = true
					job.Locations = append(job.Locations, location)
				}
			}
		}
		if len(job.Locations) == 0 {
			if address := cleanBeisenString(row["DetailAddress"]); address != "" {
				job.Locations = []string{address}
			}
		}
		if kind := cleanBeisenString(row["Kind"]); kind != "" {
			job.EmploymentType = kind
		}
		for out, key := range map[string]string{"job_ad_id": "JobAdId", "category": "Category", "category_id": "CategoryId", "organization": "Org", "organization_id": "OrgId", "degree": "Degree", "years_of_working": "YearsOfWorking", "salary": "Salary"} {
			switch value := row[key].(type) {
			case string, json.Number:
				job.Metadata[out] = value
			}
		}
		sections := []string{}
		for _, pair := range [][2]string{{"Responsibilities", "Duty"}, {"Requirements", "Require"}} {
			if body := beisenTextHTML(row[pair[1]]); body != "" {
				sections = append(sections, "<h3>"+pair[0]+"</h3>", body)
			}
		}
		if len(sections) > 0 {
			job.Description = strings.Join(sections, "\n")
		}
		jobs = append(jobs, job)
	}
	return jobs, n, nil
}

var beisenParagraphs = regexp.MustCompile(`\n\s*\n+`)

func beisenTextHTML(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	s = strings.ReplaceAll(html.UnescapeString(s), "\r\n", "\n")
	escape := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")
	paragraphs := []string{}
	for _, p := range beisenParagraphs.Split(s, -1) {
		lines := []string{}
		for _, line := range strings.Split(p, "\n") {
			if line = strings.TrimSpace(line); line != "" {
				lines = append(lines, escape.Replace(line))
			}
		}
		if len(lines) > 0 {
			paragraphs = append(paragraphs, "<p>"+strings.Join(lines, "<br>\n")+"</p>")
		}
	}
	return strings.Join(paragraphs, "\n")
}
