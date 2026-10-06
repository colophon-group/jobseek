package apisniffer

import (
	"encoding/json"
	"fmt"
	"html"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

// Comeet exposes one public dataset through a hosted assignment or a configured API.
// An API token is carried only to the exact positions endpoint selected here.
type ComeetOptions struct {
	Endpoint string
	API      bool
}

var comeetCompanyID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9.]*$`)

func ComeetOptionsFromMetadata(source, raw string) (ComeetOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return ComeetOptions{}, ErrOptions
	}
	for _, k := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return ComeetOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return ComeetOptions{}, ErrOptions
	}
	u, _ := url.Parse(source)
	company, co := m["company_id"].(string)
	token, to := m["token"].(string)
	if detailTruthy(m["company_id"]) && detailTruthy(m["token"]) && (!co || !to) {
		return ComeetOptions{}, ErrOptions
	}
	if company == "" || token == "" {
		decoded := html.UnescapeString(strings.ReplaceAll(strings.ReplaceAll(source, `\u0026`, "&"), `\x26`, "&"))
		a, e := url.Parse(decoded)
		if e == nil && (strings.EqualFold(a.Hostname(), "comeet.co") || strings.EqualFold(a.Hostname(), "www.comeet.co")) {
			parts := strings.Split(strings.Trim(a.Path, "/"), "/")
			if len(parts) == 5 && parts[0] == "careers-api" && parts[1] == "2.0" && parts[2] == "company" && parts[4] == "positions" {
				company, token = parts[3], a.Query().Get("token")
			}
		}
	}
	if company != "" && token != "" {
		if !comeetCompanyID.MatchString(company) || len(company) > 256 || len(token) > 4096 || strings.ContainsAny(token, "\x00\r\n") {
			return ComeetOptions{}, ErrOptions
		}
		endpoint := "https://www.comeet.co/careers-api/2.0/company/" + company + "/positions?token=" + url.QueryEscape(token) + "&details=true"
		if !validURL(endpoint) {
			return ComeetOptions{}, ErrOptions
		}
		return ComeetOptions{endpoint, true}, nil
	}
	if strings.EqualFold(u.Hostname(), "comeet.com") || strings.EqualFold(u.Hostname(), "www.comeet.com") {
		p := []string{}
		for _, v := range strings.Split(u.Path, "/") {
			if v != "" {
				p = append(p, v)
			}
		}
		if len(p) >= 3 && p[0] == "jobs" {
			endpoint := "https://www.comeet.com/jobs/" + url.PathEscape(p[1]) + "/" + url.PathEscape(p[2])
			if !validURL(endpoint) {
				return ComeetOptions{}, ErrOptions
			}
			return ComeetOptions{endpoint, false}, nil
		}
	}
	return ComeetOptions{source, false}, nil
}
func (o ComeetOptions) ResourceMatches(s string) bool { return s == o.Endpoint }
func ComeetPositions(raw []byte, o ComeetOptions) ([]any, error) {
	var v any
	if o.API {
		d, e := Decode(raw)
		if e != nil {
			return nil, e
		}
		v = d.Value
		if m, ok := v.(map[string]any); ok {
			v = m["positions"]
		}
	} else {
		marker := "COMPANY_POSITIONS_DATA = "
		i := strings.Index(string(raw), marker)
		if i < 0 {
			return nil, ErrInventory
		}
		d := json.NewDecoder(strings.NewReader(strings.TrimLeft(string(raw)[i+len(marker):], " \r\n\t")))
		d.UseNumber()
		if d.Decode(&v) != nil {
			return nil, ErrInventory
		}
	}
	rows, ok := v.([]any)
	if !ok {
		return nil, ErrInventory
	}
	return rows, nil
}
func comeetText(v any) any {
	if detailTruthy(v) {
		return v
	}
	return nil
}
func ComeetFields(value any) map[string]any {
	row, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	source := ""
	for _, key := range []string{"url_active_page", "url_comeet_hosted_page", "url_recruit_hosted_page", "url_detected_page"} {
		if s, ok := row[key].(string); ok && strings.TrimSpace(s) != "" {
			source = s
			break
		}
	}
	if source == "" {
		return nil
	}
	var locations any
	switch loc := row["location"].(type) {
	case string:
		if s := strings.TrimSpace(loc); s != "" {
			locations = []string{s}
		}
	case map[string]any:
		if s, ok := loc["name"].(string); ok && strings.TrimSpace(s) != "" {
			locations = []string{strings.TrimSpace(s)}
		} else {
			p := []string{}
			for _, k := range []string{"city", "state", "country"} {
				if detailTruthy(loc[k]) {
					p = append(p, strings.TrimSpace(comeetString(loc[k])))
				}
			}
			if len(p) > 0 {
				locations = []string{strings.Join(p, ", ")}
			}
		}
	}
	details, ok := row["details"].([]any)
	if !ok {
		m, _ := row["custom_fields"].(map[string]any)
		details, _ = m["details"].([]any)
	}
	valid := []map[string]any{}
	for _, d := range details {
		if m, ok := d.(map[string]any); ok {
			valid = append(valid, m)
		}
	}
	order := func(m map[string]any) int64 {
		if n, ok := m["order"].(json.Number); ok {
			if v, e := n.Int64(); e == nil {
				return v
			}
		}
		if b, ok := m["order"].(bool); ok && b {
			return 1
		}
		return 0
	}
	sort.SliceStable(valid, func(i, j int) bool { return order(valid[i]) < order(valid[j]) })
	sections, qualifications, responsibilities := []string{}, []string{}, []string{}
	for _, d := range valid {
		body, ok := d["value"].(string)
		if !ok || strings.TrimSpace(body) == "" {
			continue
		}
		body = strings.TrimSpace(body)
		name := "Details"
		if detailTruthy(d["name"]) {
			name = strings.TrimSpace(comeetString(d["name"]))
		}
		// Match Python html.escape's apostrophe spelling as well as its other escapes.
		label := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\"", "&quot;", "'", "&#x27;").Replace(name)
		sections = append(sections, "<h3>"+label+"</h3>\n"+body)
		lower := strings.ReplaceAll(strings.ToLower(name), "’", "'")
		for _, s := range []string{"requirement", "qualification", "skill set", "who you are", "what you bring"} {
			if strings.Contains(lower, s) {
				qualifications = append(qualifications, body)
				break
			}
		}
		for _, s := range []string{"responsibilit", "what you'll", "what you will"} {
			if strings.Contains(lower, s) {
				responsibilities = append(responsibilities, body)
				break
			}
		}
	}
	var description any
	if len(sections) > 0 {
		description = strings.Join(sections, "\n")
	}
	var extras any
	m := map[string]any{}
	if len(qualifications) > 0 {
		m["qualifications"] = strings.Join(qualifications, "\n")
	}
	if len(responsibilities) > 0 {
		m["responsibilities"] = strings.Join(responsibilities, "\n")
	}
	if len(m) > 0 {
		extras = m
	}
	var metadata any
	md := map[string]any{}
	for _, k := range []string{"uid", "department", "experience_level", "company_name", "time_updated"} {
		if row[k] != nil && row[k] != "" {
			md[k] = row[k]
		}
	}
	if len(md) > 0 {
		metadata = md
	}
	workplace := row["workplace_type"]
	if !detailTruthy(workplace) {
		if loc, ok := row["location"].(map[string]any); ok && detailTruthy(loc["is_remote"]) {
			workplace = "remote"
		}
	}
	return map[string]any{"url": source, "title": comeetText(row["name"]), "description": description, "locations": locations, "employment_type": comeetText(row["employment_type"]), "job_location_type": comeetText(workplace), "date_posted": comeetText(row["time_updated"]), "language": nil, "metadata": metadata, "extras": extras}
}

func comeetString(v any) string {
	switch x := v.(type) {
	case bool:
		if x {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	default:
		return fmt.Sprint(v)
	}
}
