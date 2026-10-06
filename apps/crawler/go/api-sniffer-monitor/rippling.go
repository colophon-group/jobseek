package apisniffer

import (
	"net/url"
	"reflect"
	"regexp"
	"strings"
)

type RipplingOptions struct{ Slug string }

var ripplingHost = regexp.MustCompile(`^ats\.[A-Za-z0-9_]+\.rippling\.com$`)
var ripplingWord = regexp.MustCompile(`^[\pL\pN_-]+$`)
var ripplingBoard = regexp.MustCompile(`^/(?:[a-z]{2}-[A-Z]{2}/)?([\pL\pN_-]+)/jobs(?:/|$)`)

func RipplingSlugFromURL(source string) string {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	if host != "ats.rippling.com" && !ripplingHost.MatchString(host) {
		return ""
	}
	m := ripplingBoard.FindStringSubmatch(u.Path)
	if len(m) != 2 {
		return ""
	}
	switch m[1] {
	case "api", "platform", "static", "assets", "js", "css":
		return ""
	}
	return m[1]
}
func RipplingOptionsFromMetadata(source, raw string) (RipplingOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return RipplingOptions{}, ErrOptions
	}
	for _, k := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[k]) {
			return RipplingOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return RipplingOptions{}, ErrOptions
	}
	slug, ok := m["slug"].(string)
	if detailTruthy(m["slug"]) && !ok {
		return RipplingOptions{}, ErrOptions
	}
	if slug == "" {
		slug = RipplingSlugFromURL(source)
	}
	if !ripplingWord.MatchString(slug) || len(slug) > 256 {
		return RipplingOptions{}, ErrOptions
	}
	return RipplingOptions{slug}, nil
}
func (o RipplingOptions) ListingURL() string {
	return "https://api.rippling.com/platform/api/ats/v1/board/" + url.PathEscape(o.Slug) + "/jobs"
}
func (o RipplingOptions) JobURL(id string) string {
	return "https://ats.rippling.com/" + o.Slug + "/jobs/" + id
}
func (o RipplingOptions) DetailURL(id string) string {
	return o.ListingURL() + "/" + url.PathEscape(id)
}
func (o RipplingOptions) ResourceMatches(s string) bool { return s == o.ListingURL() }
func RipplingDetailRoute(source, override string) (RipplingOptions, string, error) {
	slug := RipplingSlugFromURL(source)
	if slug == "" {
		return RipplingOptions{}, "", ErrOptions
	}
	u, _ := url.Parse(source)
	m := ripplingBoard.FindStringSubmatchIndex(u.Path)
	if m == nil {
		return RipplingOptions{}, "", ErrOptions
	}
	rest := u.Path[m[1]:]
	// The board matcher consumes the slash after jobs; the reference ID is one word segment.
	id := strings.SplitN(rest, "/", 2)[0]
	if !ripplingWord.MatchString(id) || len(id) > 256 {
		return RipplingOptions{}, "", ErrOptions
	}
	if override != "" {
		slug = override
	}
	if !ripplingWord.MatchString(slug) || len(slug) > 256 {
		return RipplingOptions{}, "", ErrOptions
	}
	return RipplingOptions{slug}, id, nil
}
func RipplingListing(d *Document, o RipplingOptions) ([]string, bool, error) {
	rows, ok := d.Value.([]any)
	if !ok {
		return []string{}, false, nil
	}
	urls := []string{}
	seen := map[string]bool{}
	count := 0
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, false, ErrInventory
		}
		value := row["uuid"]
		if !detailTruthy(value) {
			continue
		}
		id, ok := value.(string)
		if !ok || !ripplingWord.MatchString(id) || len(id) > 256 {
			return nil, false, ErrInventory
		}
		count++
		source := o.JobURL(id)
		if !seen[source] {
			seen[source] = true
			urls = append(urls, source)
		}
	}
	return urls, count > 50000, nil
}
func RipplingDetailFields(d *Document) (map[string]any, error) {
	row, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	description := []string{}
	if detailTruthy(row["description"]) {
		m, ok := row["description"].(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		for _, k := range []string{"company", "role"} {
			if detailTruthy(m[k]) {
				s, ok := m[k].(string)
				if !ok {
					return nil, ErrInventory
				}
				description = append(description, s)
			}
		}
	}
	var desc any
	if len(description) > 0 {
		desc = strings.Join(description, "\n")
	}
	var locations any
	locs := []string{}
	if detailTruthy(row["workLocations"]) {
		raw, ok := row["workLocations"].([]any)
		if !ok {
			return nil, ErrInventory
		}
		for _, v := range raw {
			if detailTruthy(v) {
				s, ok := v.(string)
				if !ok {
					return nil, ErrInventory
				}
				locs = append(locs, s)
			}
		}
	}
	if len(locs) > 0 {
		locations = locs
	}
	var employment any
	if detailTruthy(row["employmentType"]) {
		m, ok := row["employmentType"].(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		employment = comeetText(m["label"])
		if employment == nil {
			employment = comeetText(m["id"])
		}
	}
	md := map[string]any{}
	if dept, ok := row["department"].(map[string]any); ok {
		if detailTruthy(dept["name"]) {
			md["department"] = dept["name"]
		}
		if detailTruthy(dept["base_department"]) && !reflect.DeepEqual(dept["base_department"], dept["name"]) {
			md["base_department"] = dept["base_department"]
		}
	}
	if detailTruthy(row["companyName"]) {
		md["company"] = row["companyName"]
	}
	var metadata any
	if len(md) > 0 {
		metadata = md
	}
	var salary any
	if detailTruthy(row["payRangeDetails"]) {
		ranges, ok := row["payRangeDetails"].([]any)
		if !ok || len(ranges) == 0 {
			return nil, ErrInventory
		}
		pr, ok := ranges[0].(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		if pr["rangeStart"] != nil || pr["rangeEnd"] != nil {
			unit := "year"
			if pr["frequency"] != nil {
				raw, ok := pr["frequency"].(string)
				if !ok {
					return nil, ErrInventory
				}
				key := strings.ToLower(strings.TrimSpace(raw))
				switch key {
				case "hr":
					unit = "hour"
				case "mo":
					unit = "month"
				case "yr":
					unit = "year"
				default:
					for _, p := range [][2]string{{"two_weeks", "week"}, {"biweekly", "week"}, {"hour", "hour"}, {"month", "month"}, {"week", "week"}, {"year", "year"}, {"annual", "year"}, {"daily", "day"}, {"day", "day"}} {
						if strings.Contains(key, p[0]) {
							unit = p[1]
							break
						}
					}
				}
			}
			salary = map[string]any{"currency": pr["currency"], "min": pr["rangeStart"], "max": pr["rangeEnd"], "unit": unit}
		}
	}
	return map[string]any{"title": row["name"], "description": desc, "locations": locations, "employment_type": employment, "job_location_type": nil, "date_posted": row["createdOn"], "base_salary": salary, "language": nil, "extras": nil, "metadata": metadata}, nil
}
