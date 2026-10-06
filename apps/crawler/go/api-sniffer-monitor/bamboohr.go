package apisniffer

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
	"unicode"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/text/cases"
)

type BambooHROptions struct{ Tenant, DescriptionInclude string }

var bambooTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)

func normalizeBambooTenant(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	s = strings.ToLower(strings.TrimSpace(s))
	if !bambooTenant.MatchString(s) || s == "api" || s == "app" || s == "help" || s == "static" || s == "www" {
		return ""
	}
	return s
}
func BambooHROptionsFromMetadata(source, raw string) (BambooHROptions, error) {
	md, err := DecodeInlineMetadata(raw)
	if err != nil || !validURL(source) {
		return BambooHROptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(md[key]) {
			return BambooHROptions{}, ErrOptions
		}
	}
	if md["ssl_verify"] != nil && md["ssl_verify"] != true {
		return BambooHROptions{}, ErrOptions
	}
	tenant := normalizeBambooTenant(md["tenant"])
	if tenant == "" {
		u, _ := url.Parse(source)
		host := strings.ToLower(u.Hostname())
		path := strings.ToLower(strings.TrimRight(u.Path, "/"))
		if strings.HasSuffix(host, ".bamboohr.com") && (path == "/careers" || path == "/careers/list" || path == "/jobs/embed2.php") {
			tenant = normalizeBambooTenant(strings.TrimSuffix(host, ".bamboohr.com"))
		}
	}
	if tenant == "" {
		return BambooHROptions{}, ErrOptions
	}
	filter := ""
	if value := md["description_include_regex"]; value != nil {
		var ok bool
		filter, ok = value.(string)
		filter = strings.TrimSpace(filter)
		if !ok || filter == "" || len([]rune(filter)) > 1000 {
			return BambooHROptions{}, ErrOptions
		}
		if _, err := dom.CompileURLPattern(filter); err != nil {
			return BambooHROptions{}, ErrOptions
		}
	}
	return BambooHROptions{tenant, filter}, nil
}
func (o BambooHROptions) ListingURL() string {
	return "https://" + o.Tenant + ".bamboohr.com/careers/list"
}
func (o BambooHROptions) JobURL(id string) string {
	return "https://" + o.Tenant + ".bamboohr.com/careers/" + id
}
func (o BambooHROptions) DetailURL(id string) string { return o.JobURL(id) + "/detail" }
func (o BambooHROptions) ResourceMatches(source string) bool {
	if source == o.ListingURL() {
		return true
	}
	u, err := url.Parse(source)
	if err != nil || !validURL(source) || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || u.Host != o.Tenant+".bamboohr.com" {
		return false
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	return len(parts) == 3 && parts[0] == "careers" && parts[2] == "detail" && bambooID(parts[1]) != ""
}
func bambooID(value any) string {
	s := ""
	switch v := value.(type) {
	case string:
		s = strings.TrimSpace(v)
	case json.Number:
		s = v.String()
	}
	if s == "" {
		return ""
	}
	for _, r := range s {
		if !unicode.IsDigit(r) {
			return ""
		}
	}
	return s
}
func bambooClean(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	return strings.TrimSpace(s)
}
func bambooLocations(raw map[string]any) any {
	if s, ok := raw["location"].(string); ok {
		if s = bambooClean(s); s != "" {
			return []string{s}
		}
		return nil
	}
	location, _ := raw["location"].(map[string]any)
	if s, ok := raw["atsLocation"].(string); ok {
		if s = bambooClean(s); s != "" {
			return []string{s}
		}
	}
	ats, _ := raw["atsLocation"].(map[string]any)
	first := func(values ...any) any {
		for _, v := range values {
			if detailTruthy(v) {
				return v
			}
		}
		return nil
	}
	fields := [][2]any{{location["city"], ats["city"]}, {first(location["state"], location["province"]), first(ats["state"], ats["province"])}, {first(location["addressCountry"], location["country"]), ats["country"]}}
	out := []string{}
	seen := map[string]bool{}
	fold := cases.Fold()
	for _, pair := range fields {
		s := bambooClean(pair[0])
		if s == "" {
			s = bambooClean(pair[1])
		}
		if s != "" && !seen[fold.String(s)] {
			seen[fold.String(s)] = true
			out = append(out, s)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return []string{strings.Join(out, ", ")}
}
func BambooHRFields(raw any, o BambooHROptions) map[string]any {
	row, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id := bambooID(row["id"])
	if id == "" {
		return nil
	}
	var title, employment, locationType any
	if v := bambooClean(row["jobOpeningName"]); v != "" {
		title = v
	}
	if v := bambooClean(row["employmentStatusLabel"]); v != "" {
		employment = v
	}
	if value := row["locationType"]; value != nil {
		key := ""
		switch v := value.(type) {
		case string:
			key = v
		case json.Number:
			key = v.String()
		}
		if s := map[string]string{"0": "onsite", "1": "remote", "2": "hybrid"}[key]; s != "" {
			locationType = s
		}
	} else if row["isRemote"] == true {
		locationType = "remote"
	}
	md := map[string]any{"job_id": id}
	for source, target := range map[string]string{"departmentLabel": "department", "departmentId": "department_id"} {
		if v := row[source]; v != nil && v != "" {
			md[target] = v
		}
	}
	return map[string]any{"url": o.JobURL(id), "title": title, "description": nil, "locations": bambooLocations(row), "employment_type": employment, "job_location_type": locationType, "date_posted": nil, "language": nil, "metadata": md, "extras": nil}
}
func BambooHRListing(d *Document, o BambooHROptions) ([]map[string]any, bool, error) {
	payload, ok := d.Value.(map[string]any)
	if !ok {
		return nil, false, ErrInventory
	}
	rows, ok := payload["result"].([]any)
	if !ok {
		return nil, false, ErrInventory
	}
	meta, _ := payload["meta"].(map[string]any)
	n, ok := meta["totalCount"].(json.Number)
	if !ok {
		return nil, false, ErrInventory
	}
	total, err := n.Int64()
	if err != nil || total < 0 || total < int64(len(rows)) {
		return nil, false, ErrInventory
	}
	out := []map[string]any{}
	seen := map[string]bool{}
	truncated := total > int64(len(rows))
	for _, raw := range rows {
		job := BambooHRFields(raw, o)
		if job == nil {
			truncated = true
			continue
		}
		source := job["url"].(string)
		if seen[source] {
			truncated = true
			continue
		}
		seen[source] = true
		if len(out) < 50000 {
			out = append(out, job)
		} else {
			truncated = true
		}
	}
	if len(rows) > 0 && len(out) == 0 {
		return nil, false, ErrInventory
	}
	return out, truncated, nil
}
