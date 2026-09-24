package lever

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

const (
	MaxJobs   = 50_000
	BatchSize = 100
)

type Job struct {
	URL             string         `json:"url"`
	Title           any            `json:"title"`
	Description     *string        `json:"description"`
	Locations       []string       `json:"locations"`
	EmploymentType  any            `json:"employment_type"`
	JobLocationType any            `json:"job_location_type"`
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

func pythonText(value any) string {
	if value == nil {
		return "None"
	}
	return fmt.Sprint(value)
}

func description(raw map[string]any) (*string, error) {
	parts := []string{}
	if truthy(raw["description"]) {
		value, ok := raw["description"].(string)
		if !ok {
			return nil, errors.New("Lever description must be text")
		}
		parts = append(parts, value)
	}
	if value, exists := raw["lists"]; exists {
		items, ok := value.([]any)
		if !ok {
			return nil, errors.New("Lever lists must be an array")
		}
		for _, item := range items {
			entry, ok := item.(map[string]any)
			if !ok {
				return nil, errors.New("Lever list item must be an object")
			}
			text, hasText := entry["text"]
			if !hasText {
				text = ""
			}
			content, hasContent := entry["content"]
			if !hasContent {
				content = ""
			}
			if truthy(text) || truthy(content) {
				parts = append(parts, "<h3>"+pythonText(text)+"</h3><ul>"+pythonText(content)+"</ul>")
			}
		}
	}
	if truthy(raw["additional"]) {
		value, ok := raw["additional"].(string)
		if !ok {
			return nil, errors.New("Lever additional must be text")
		}
		parts = append(parts, value)
	}
	if len(parts) == 0 {
		return nil, nil
	}
	joined := strings.Join(parts, "\n")
	return &joined, nil
}

func salaryUnit(value any) any {
	raw, ok := value.(string)
	if !ok {
		return value
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
	return raw
}

func salary(raw any) (map[string]any, error) {
	if !truthy(raw) {
		return nil, nil
	}
	rangeValue, ok := raw.(map[string]any)
	if !ok {
		return nil, errors.New("Lever salaryRange must be an object")
	}
	if rangeValue["min"] == nil && rangeValue["max"] == nil {
		return nil, nil
	}
	interval, exists := rangeValue["interval"]
	if !exists {
		interval = ""
	}
	return map[string]any{
		"currency": rangeValue["currency"],
		"min":      rangeValue["min"],
		"max":      rangeValue["max"],
		"unit":     salaryUnit(interval),
	}, nil
}

func parseJob(raw map[string]any) (Job, bool, error) {
	url, ok := raw["hostedUrl"].(string)
	if !ok || url == "" {
		return Job{}, false, nil
	}
	var categories map[string]any
	if rawValue, exists := raw["categories"]; exists {
		var ok bool
		categories, ok = rawValue.(map[string]any)
		if !ok {
			return Job{}, false, errors.New("Lever categories must be an object")
		}
	} else {
		categories = map[string]any{}
	}
	var locations []string
	if rawLocations, exists := categories["allLocations"]; exists && truthy(rawLocations) {
		items, ok := rawLocations.([]any)
		if !ok {
			return Job{}, false, errors.New("Lever allLocations must be an array")
		}
		for _, item := range items {
			location, ok := item.(string)
			if !ok {
				return Job{}, false, errors.New("Lever location must be text")
			}
			locations = append(locations, location)
		}
	}
	if len(locations) == 0 && truthy(categories["location"]) {
		location, ok := categories["location"].(string)
		if !ok {
			return Job{}, false, errors.New("Lever location must be text")
		}
		locations = []string{location}
	}
	metadata := map[string]any{}
	for _, entry := range []struct{ source, target string }{{"team", "team"}, {"department", "department"}} {
		if truthy(categories[entry.source]) {
			metadata[entry.target] = categories[entry.source]
		}
	}
	if truthy(raw["id"]) {
		metadata["id"] = raw["id"]
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	desc, err := description(raw)
	if err != nil {
		return Job{}, false, err
	}
	baseSalary, err := salary(raw["salaryRange"])
	if err != nil {
		return Job{}, false, err
	}
	return Job{URL: url, Title: raw["text"], Description: desc, Locations: locations, EmploymentType: categories["commitment"], JobLocationType: raw["workplaceType"], BaseSalary: baseSalary, Metadata: metadata}, true, nil
}

func ParsePage(body []byte) ([]Job, int, error) {
	var root any
	if err := json.Unmarshal(body, &root); err != nil {
		return nil, 0, err
	}
	items, ok := root.([]any)
	if !ok {
		return nil, 0, errors.New("Lever page must be a JSON array")
	}
	jobs := make([]Job, 0, len(items))
	for index, value := range items {
		item, ok := value.(map[string]any)
		if !ok {
			return nil, 0, fmt.Errorf("Lever item %d must be an object", index)
		}
		job, included, err := parseJob(item)
		if err != nil {
			return nil, 0, fmt.Errorf("Lever item %d: %w", index, err)
		}
		if included {
			jobs = append(jobs, job)
		}
	}
	return jobs, len(items), nil
}
