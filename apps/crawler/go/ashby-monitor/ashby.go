package ashby

import (
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
)

const MaxJobs = 50_000

type Job struct {
	URL             string         `json:"url"`
	Title           any            `json:"title"`
	Description     any            `json:"description"`
	Locations       []string       `json:"locations"`
	EmploymentType  any            `json:"employment_type"`
	JobLocationType *string        `json:"job_location_type"`
	DatePosted      any            `json:"date_posted"`
	BaseSalary      map[string]any `json:"base_salary"`
	Metadata        map[string]any `json:"metadata"`
}

type Inventory struct {
	Jobs      []Job `json:"jobs"`
	Truncated bool  `json:"truncated"`
}

func truthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case float64:
		return v != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}

func locationType(value any) *string {
	raw, ok := value.(string)
	if !ok {
		return nil
	}
	var normalized string
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "onsite", "on-site", "on_site", "on site":
		normalized = "onsite"
	case "remote":
		normalized = "remote"
	case "hybrid":
		normalized = "hybrid"
	default:
		return nil
	}
	return &normalized
}

func salaryUnit(value any) string {
	raw, ok := value.(string)
	if !ok {
		return "year"
	}
	key := strings.ToLower(strings.TrimSpace(raw))
	switch key {
	case "yr", "annual", "annually", "per year", "per-year", "per-year-salary", "yearly_annually", "year", "yearly":
		return "year"
	case "mo", "monthly", "per month", "per-month", "per-month-salary", "month":
		return "month"
	case "weekly", "per week", "per-week", "two_weeks", "biweekly", "week":
		return "week"
	case "daily", "per day", "per-day", "day":
		return "day"
	case "hr", "hourly", "per hour", "per-hour", "per-hour-wage", "hour":
		return "hour"
	}
	for _, entry := range []struct{ needle, canonical string }{{"two_weeks", "week"}, {"biweekly", "week"}, {"hour", "hour"}, {"month", "month"}, {"week", "week"}, {"year", "year"}, {"annual", "year"}, {"daily", "day"}, {"day", "day"}} {
		if strings.Contains(key, entry.needle) {
			return entry.canonical
		}
	}
	return "year"
}

func parseSalary(job map[string]any, compensation map[string]any) (map[string]any, error) {
	if len(compensation) == 0 || !truthy(job["compensationTierSummary"]) {
		return nil, nil
	}
	rawTiers, ok := compensation["compensationTierSummary"]
	if !ok || !truthy(rawTiers) {
		return nil, nil
	}
	tiers, ok := rawTiers.([]any)
	if !ok {
		return nil, errors.New("Ashby compensationTierSummary must be a list")
	}
	for _, raw := range tiers {
		tier, ok := raw.(map[string]any)
		if !ok {
			return nil, errors.New("Ashby compensation tier must be an object")
		}
		if !reflect.DeepEqual(tier["id"], job["compensationTierSummary"]) {
			continue
		}
		if tier["min"] == nil && tier["max"] == nil {
			return nil, nil
		}
		return map[string]any{"currency": tier["currency"], "min": tier["min"], "max": tier["max"], "unit": salaryUnit(tier["interval"])}, nil
	}
	return nil, nil
}

func parseLocations(raw map[string]any) ([]string, error) {
	locations := []string{}
	seen := map[string]bool{}
	add := func(value string) {
		if value != "" && !seen[value] {
			locations = append(locations, value)
			seen[value] = true
		}
	}
	if primary, ok := raw["location"].(string); ok {
		add(primary)
	}
	if secondary, exists := raw["secondaryLocations"]; exists {
		items, ok := secondary.([]any)
		if !ok {
			return nil, errors.New("Ashby secondaryLocations must be a list")
		}
		for _, item := range items {
			if value, ok := item.(string); ok {
				add(value)
				continue
			}
			object, ok := item.(map[string]any)
			if !ok {
				return nil, errors.New("Ashby secondary location must be a string or object")
			}
			if value, ok := object["location"].(string); ok {
				add(value)
			}
		}
	}
	if len(locations) == 0 {
		if address, ok := raw["address"].(map[string]any); ok {
			parts := []string{}
			for _, field := range []string{"city", "region", "country"} {
				if truthy(address[field]) {
					value, ok := address[field].(string)
					if !ok {
						return nil, fmt.Errorf("Ashby address %s must be text", field)
					}
					parts = append(parts, value)
				}
			}
			if len(parts) > 0 {
				add(strings.Join(parts, ", "))
			}
		}
	}
	if len(locations) == 0 {
		return nil, nil
	}
	return locations, nil
}

func parseJob(raw map[string]any, compensation map[string]any) (Job, bool, error) {
	if !truthy(raw["isListed"]) {
		if _, exists := raw["isListed"]; exists {
			return Job{}, false, nil
		}
	}
	url, ok := raw["jobUrl"].(string)
	if !ok || url == "" {
		return Job{}, false, nil
	}
	locations, err := parseLocations(raw)
	if err != nil {
		return Job{}, false, err
	}
	salary, err := parseSalary(raw, compensation)
	if err != nil {
		return Job{}, false, err
	}
	description := raw["descriptionHtml"]
	if !truthy(description) {
		description = raw["descriptionPlain"]
	}
	employment := raw["employmentType"]
	if !truthy(employment) {
		employment = nil
	}
	metadata := map[string]any{}
	for _, field := range []string{"department", "team", "id"} {
		if truthy(raw[field]) {
			metadata[field] = raw[field]
		}
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	return Job{URL: url, Title: raw["title"], Description: description, Locations: locations, EmploymentType: employment, JobLocationType: locationType(raw["workplaceType"]), DatePosted: raw["publishedAt"], BaseSalary: salary, Metadata: metadata}, true, nil
}

func Parse(body []byte) (Inventory, error) {
	var root map[string]any
	if err := json.Unmarshal(body, &root); err != nil || root == nil {
		return Inventory{}, errors.New("Ashby response must be a JSON object")
	}
	rawJobs, exists := root["jobs"]
	if !exists {
		rawJobs = []any{}
	}
	jobs, ok := rawJobs.([]any)
	if !ok {
		return Inventory{}, errors.New("Ashby jobs must be a list")
	}
	var compensation map[string]any
	if raw, exists := root["compensation"]; exists && truthy(raw) {
		var ok bool
		compensation, ok = raw.(map[string]any)
		if !ok {
			return Inventory{}, errors.New("Ashby compensation must be an object")
		}
	}
	result := Inventory{Jobs: make([]Job, 0, len(jobs))}
	for index, raw := range jobs {
		item, ok := raw.(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Ashby job %d must be an object", index)
		}
		job, listed, err := parseJob(item, compensation)
		if err != nil {
			return Inventory{}, fmt.Errorf("Ashby job %d: %w", index, err)
		}
		if listed {
			result.Jobs = append(result.Jobs, job)
		}
	}
	result.Truncated = len(result.Jobs) > MaxJobs
	return result, nil
}
