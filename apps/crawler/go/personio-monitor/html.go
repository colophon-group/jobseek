package personio

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

func nonempty(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case string:
		return typed != ""
	case bool:
		return typed
	case float64:
		return typed != 0
	default:
		return true
	}
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func rawString(value any) *string {
	text, ok := value.(string)
	if !ok || text == "" {
		return nil
	}
	return &text
}

// ParseHTMLListings mirrors the existing RSC job-array fallback. The caller
// uses it only after both XML feed domains are unavailable.
func ParseHTMLListings(body, slug, domain string) ([]Job, error) {
	if !slugPattern.MatchString(slug) || domain != "de" && domain != "com" {
		return nil, fmt.Errorf("invalid Personio slug or domain")
	}
	escapedSlug := regexp.QuoteMeta(slug)
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`(?s)\\"jobs\\":\s*(\[\{.*?\}\])\s*,\s*\\"subdomain\\":\s*\\"` + escapedSlug + `\\"`),
		regexp.MustCompile(`(?s)"jobs":\s*(\[\{.*?\}\])\s*,\s*"subdomain"\s*:\s*"` + escapedSlug + `"`),
	}
	var rawJSON string
	for _, pattern := range patterns {
		if match := pattern.FindStringSubmatch(body); len(match) == 2 {
			rawJSON = match[1]
			break
		}
	}
	if rawJSON == "" {
		return nil, nil
	}
	rawJSON = strings.ReplaceAll(rawJSON, `\"`, `"`)
	var rows []map[string]any
	if err := json.Unmarshal([]byte(rawJSON), &rows); err != nil {
		return nil, nil
	}
	jobs := make([]Job, 0, len(rows))
	for _, row := range rows {
		if !nonempty(row["id"]) {
			continue
		}
		id := stringValue(row["id"])
		metadata := map[string]any{"id": id}
		for _, field := range []struct{ source, target string }{
			{"department", "department"}, {"subcompany", "subcompany"},
			{"category", "recruitingCategory"}, {"seniority", "seniority"},
			{"years_of_experience", "yearsOfExperience"}, {"occupation", "occupation"},
			{"occupation_category", "occupationCategory"}, {"keywords", "keywords"},
		} {
			if nonempty(row[field.source]) {
				metadata[field.target] = row[field.source]
			}
		}
		var locations []string
		if office := rawString(row["main_office"]); office != nil {
			locations = []string{*office}
		}
		employment := rawString(row["employment_type"])
		if employment != nil {
			value := strings.ToLower(*employment)
			employment = &value
		}
		if employment == nil || *employment != "intern" && *employment != "trainee" && *employment != "freelance" {
			if schedule := rawString(row["schedule"]); schedule != nil {
				value := strings.ToLower(*schedule)
				employment = &value
			} else {
				employment = nil
			}
		}
		jobs = append(jobs, Job{
			URL:   "https://" + slug + ".jobs.personio." + domain + "/job/" + id,
			Title: rawString(row["name"]), Locations: locations,
			EmploymentType: employment, DatePosted: rawString(row["created_at"]),
			Metadata: metadata,
		})
	}
	return jobs, nil
}
