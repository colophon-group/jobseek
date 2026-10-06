package apisniffer

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/base64"
	"encoding/json"
	"html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type MokahrPartition struct {
	PageURL, Origin, Path, OrgID string
	SiteID                       int
}
type MokahrOptions struct {
	Partitions []MokahrPartition
	Locale     string
}

var mokahrID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var mokahrSiteID = regexp.MustCompile(`^[1-9][0-9]{0,11}$`)
var mokahrRoute = regexp.MustCompile(`(?i)^(social|campus)[_-](recruitment|apply)$`)
var mokahrLocale = regexp.MustCompile(`^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8}){0,2}$`)
var mokahrInit = regexp.MustCompile(`id="init-data"[^>]*value="([^"]*)"`)
var MokahrClosedPage = regexp.MustCompile(`(?i)<title[^>]*>\s*当前网页已关停\s*</title>`)

func mokahrSite(value any) (int, bool) {
	s, ok := value.(string)
	if !ok {
		n, number := value.(json.Number)
		if !number {
			return 0, false
		}
		s = string(n)
	}
	if !mokahrSiteID.MatchString(s) {
		return 0, false
	}
	n, e := strconv.Atoi(s)
	return n, e == nil
}
func mokahrPartition(raw, org string, site int, required bool) (MokahrPartition, error) {
	u, e := url.Parse(raw)
	if e != nil || len(raw) > 2048 || !validURL(strings.Split(raw, "#")[0]) || u.RawPath != "" {
		return MokahrPartition{}, ErrOptions
	}
	host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	if host == "" {
		return MokahrPartition{}, ErrOptions
	}
	origin := "https://" + host
	segments := []string{}
	for _, s := range strings.Split(u.Path, "/") {
		if s != "" {
			segments = append(segments, s)
		}
	}
	path := "social-recruitment"
	exact := len(segments) == 3
	if exact {
		_, exact = mokahrSite(segments[2])
		exact = exact && mokahrRoute.MatchString(segments[0]) && mokahrID.MatchString(segments[1])
	}
	if exact {
		n, _ := mokahrSite(segments[2])
		if segments[1] != org || n != site {
			return MokahrPartition{}, ErrOptions
		}
		path = segments[0]
	} else {
		if required {
			return MokahrPartition{}, ErrOptions
		}
		if strings.Contains(strings.ToLower(raw), "campus") {
			path = "campus-recruitment"
		}
	}
	return MokahrPartition{origin + "/" + path + "/" + org + "/" + strconv.Itoa(site), origin, path, org, site}, nil
}

func MokahrOptionsFromMetadata(boardURL, metadata string) (MokahrOptions, error) {
	d, e := Decode([]byte(metadata))
	if e != nil {
		return MokahrOptions{}, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return MokahrOptions{}, ErrOptions
	}
	for _, k := range []string{"render", "proxy", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return MokahrOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return MokahrOptions{}, ErrOptions
	}
	org, ok := m["org_id"].(string)
	if !ok || !mokahrID.MatchString(org) {
		return MokahrOptions{}, ErrOptions
	}
	site, ok := mokahrSite(m["site_id"])
	if !ok {
		return MokahrOptions{}, ErrOptions
	}
	locale := "zh-CN"
	if v, exists := m["locale"]; exists {
		locale, ok = v.(string)
		if !ok || !mokahrLocale.MatchString(locale) {
			return MokahrOptions{}, ErrOptions
		}
	}
	p, e := mokahrPartition(boardURL, org, site, false)
	if e != nil {
		return MokahrOptions{}, e
	}
	out := MokahrOptions{Partitions: []MokahrPartition{p}, Locale: locale}
	if v, exists := m["partitions"]; exists {
		rows, ok := v.([]any)
		if !ok || len(rows) > 15 {
			return MokahrOptions{}, ErrOptions
		}
		for _, v := range rows {
			row, ok := v.(map[string]any)
			if !ok || len(row) != 2 {
				return MokahrOptions{}, ErrOptions
			}
			raw, ok := row["board_url"].(string)
			if !ok {
				return MokahrOptions{}, ErrOptions
			}
			site, ok := mokahrSite(row["site_id"])
			if !ok {
				return MokahrOptions{}, ErrOptions
			}
			p, e := mokahrPartition(raw, org, site, true)
			if e != nil {
				return MokahrOptions{}, e
			}
			out.Partitions = append(out.Partitions, p)
		}
	}
	seen := map[int]bool{}
	pages := map[string]bool{}
	for _, p := range out.Partitions {
		if seen[p.SiteID] || pages[p.PageURL] {
			return MokahrOptions{}, ErrOptions
		}
		seen[p.SiteID] = true
		pages[p.PageURL] = true
	}
	return out, nil
}
func (p MokahrPartition) APIURL() string          { return p.Origin + "/api/outer/ats-apply/website/jobs/v2" }
func (p MokahrPartition) JobURL(id string) string { return p.PageURL + "#/job/" + id }
func (o MokahrOptions) ResourceMatches(raw string) bool {
	for _, p := range o.Partitions {
		if raw == p.PageURL || raw == p.APIURL() {
			return true
		}
	}
	return false
}

func MokahrBootstrap(page string, p MokahrPartition) (string, map[int]string, error) {
	m := mokahrInit.FindStringSubmatch(page)
	if len(m) != 2 {
		return "", nil, ErrInventory
	}
	d, e := Decode([]byte(html.UnescapeString(m[1])))
	if e != nil {
		return "", nil, ErrInventory
	}
	v, ok := d.Value.(map[string]any)
	if !ok {
		return "", nil, ErrInventory
	}
	iv, ok := v["aesIv"].(string)
	if !ok || len(iv) != 16 {
		return "", nil, ErrInventory
	}
	for _, r := range iv {
		if r > 127 {
			return "", nil, ErrInventory
		}
	}
	org, ok := v["org"].(map[string]any)
	if !ok || org["id"] != p.OrgID {
		return "", nil, ErrInventory
	}
	a, ok := mokahrSite(v["siteId"])
	b, ok2 := mokahrSite(org["siteId"])
	if !ok || !ok2 || a != p.SiteID || b != p.SiteID {
		return "", nil, ErrInventory
	}
	expected := "social"
	if strings.HasPrefix(strings.ToLower(p.Path), "campus") {
		expected = "camp"
	}
	if org["type"] != nil && org["type"] != expected {
		return "", nil, ErrInventory
	}
	return iv, mokahrCities(v), nil
}

func mokahrCities(v map[string]any) map[int]string {
	cities := map[int]string{}
	groups, _ := v["jobsGroupedByLocation"].([]any)
	for _, g := range groups {
		row, ok := g.(map[string]any)
		if !ok {
			continue
		}
		id, ok := integer(row["cityId"])
		label := row["label"]
		if !detailTruthy(label) {
			label = row["id"]
		}
		text, ok2 := label.(string)
		if !ok || !ok2 || text == "" {
			continue
		}
		cities[id] = text
		if id >= 100000 && id%100 != 0 {
			parent := id / 100 * 100
			if _, seen := cities[parent]; !seen {
				cities[parent] = text
			}
		}
	}
	return cities
}

// All identity and AES padding checks precede exposure of the decrypted rows.
func MokahrDecrypt(d *Document, iv string) (*Document, error) {
	if d == nil {
		return nil, ErrInventory
	}
	v, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	encoded, ok := v["data"].(string)
	key, ok2 := v["necromancer"].(string)
	if !ok || !ok2 || encoded == "" || key == "" || len(iv) != 16 {
		return nil, ErrInventory
	}
	for _, s := range []string{key, iv} {
		for _, r := range s {
			if r > 127 {
				return nil, ErrInventory
			}
		}
	}
	ct, e := base64.StdEncoding.DecodeString(encoded)
	if e != nil {
		return nil, ErrInventory
	}
	block, e := aes.NewCipher([]byte(key))
	if e != nil || len(ct) == 0 || len(ct)%aes.BlockSize != 0 {
		return nil, ErrInventory
	}
	plain := make([]byte, len(ct))
	cipher.NewCBCDecrypter(block, []byte(iv)).CryptBlocks(plain, ct)
	pad := int(plain[len(plain)-1])
	if pad < 1 || pad > aes.BlockSize || pad > len(plain) {
		return nil, ErrInventory
	}
	for _, b := range plain[len(plain)-pad:] {
		if int(b) != pad {
			return nil, ErrInventory
		}
	}
	d, e = Decode(plain[:len(plain)-pad])
	if e != nil {
		return nil, ErrInventory
	}
	return d, nil
}

func MokahrListing(d *Document, iv string, p MokahrPartition) ([]map[string]any, int, error) {
	d, e := MokahrDecrypt(d, iv)
	if e != nil {
		return nil, 0, e
	}
	outer, ok := d.Value.(map[string]any)
	if !ok || outer["success"] != true {
		return nil, 0, ErrInventory
	}
	code, ok := integer(outer["code"])
	if !ok || code != 0 {
		return nil, 0, ErrInventory
	}
	inner, ok := outer["data"].(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	stats, ok := inner["jobStats"].(map[string]any)
	if !ok || stats["orgId"] != p.OrgID {
		return nil, 0, ErrInventory
	}
	total, ok := integer(stats["total"])
	if !ok || total < 0 {
		return nil, 0, ErrInventory
	}
	raw, ok := inner["jobs"].([]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	rows := make([]map[string]any, len(raw))
	for i, v := range raw {
		row, ok := v.(map[string]any)
		if !ok {
			return nil, 0, ErrInventory
		}
		rows[i] = row
	}
	return rows, total, nil
}

func mokahrCity(id int, cities map[int]string) string {
	if v := cities[id]; v != "" {
		return v
	}
	if id < 100000 {
		return ""
	}
	if id%100 != 0 {
		if v := cities[id/100*100]; v != "" {
			return v
		}
	}
	if id%10000 != 0 {
		return cities[id/10000*10000]
	}
	return ""
}

type MokahrJob struct {
	Job
	BaseSalary map[string]any `json:"base_salary"`
}

func MokahrFields(raw map[string]any, cities map[int]string) (MokahrJob, error) {
	j := Job{Title: raw["title"], Description: raw["jobDescription"], DatePosted: raw["publishedAt"], Metadata: map[string]any{}}
	if detailTruthy(raw["commitment"]) {
		j.EmploymentType = raw["commitment"]
	}
	seen := map[string]bool{}
	locations, _ := raw["locations"].([]any)
	for _, v := range locations {
		label, ok := v.(string)
		if row, isMap := v.(map[string]any); isMap {
			city, _ := row["cityName"].(string)
			if city == "" {
				id, _ := integer(row["cityId"])
				city = mokahrCity(id, cities)
			}
			if city == "" {
				city, _ = row["provinceName"].(string)
			}
			country, _ := row["country"].(string)
			parts := []string{}
			for _, s := range []string{city, country} {
				if s != "" {
					parts = append(parts, s)
				}
			}
			label = strings.Join(parts, ", ")
			ok = true
		}
		if ok && label != "" && !seen[label] {
			j.Locations = append(j.Locations, label)
			seen[label] = true
		}
	}
	for out, key := range map[string]string{"department": "department", "education": "education", "job_function": "zhineng"} {
		value := raw[key]
		if key != "education" {
			if m, ok := value.(map[string]any); ok {
				value = m["name"]
			}
		}
		if text, ok := value.(string); ok && text != "" {
			j.Metadata[out] = text
		}
	}
	experience := map[string]any{}
	for out, key := range map[string]string{"min_years": "minExperience", "max_years": "maxExperience"} {
		if n, ok := raw[key].(json.Number); ok {
			if v, e := n.Float64(); e == nil {
				experience[out] = v
			}
		} else if b, ok := raw[key].(bool); ok {
			v := 0.0
			if b {
				v = 1
			}
			experience[out] = v
		}
	}
	if len(experience) > 0 {
		j.Extras = map[string]any{"experience": experience}
	}
	salary := map[string]any{}
	unit := ""
	mult := 1.0
	if n, ok := integer(raw["salaryUnit"]); ok {
		switch n {
		case 0:
			unit = "monthly"
			mult = 1000
		case 1, 6:
			unit = "monthly"
		case 2, 7:
			unit = "weekly"
		case 3, 8:
			unit = "daily"
		case 4, 9:
			unit = "hourly"
		case 5, 10:
			unit = "per_task"
		case 11:
			unit = "yearly"
		}
	}
	for out, key := range map[string]string{"min": "minSalary", "max": "maxSalary"} {
		salary[out] = nil
		if n, ok := raw[key].(json.Number); ok {
			if v, e := n.Float64(); e == nil && v != 0 {
				salary[out] = v * mult
			}
		} else if b, ok := raw[key].(bool); ok && b {
			salary[out] = mult
		}
	}
	var base map[string]any
	if salary["min"] != nil || salary["max"] != nil {
		salary["currency"] = "CNY"
		salary["unit"] = nil
		if unit != "" {
			salary["unit"] = unit
		}
		base = salary
	}
	return MokahrJob{j, base}, nil
}

func MokahrProject(raw map[string]any, p MokahrPartition, cities map[int]string) (MokahrJob, error) {
	id, ok := raw["id"].(string)
	title, ok2 := raw["title"].(string)
	if !ok || !ok2 || !mokahrID.MatchString(id) || strings.TrimSpace(title) == "" || raw["orgId"] != p.OrgID {
		return MokahrJob{}, ErrInventory
	}
	j, e := MokahrFields(raw, cities)
	if e != nil {
		return MokahrJob{}, e
	}
	j.URL = p.JobURL(id)
	j.Title = title
	j.Metadata["provider_id"] = id
	j.Metadata["provider_site_id"] = p.SiteID
	return j, nil
}
