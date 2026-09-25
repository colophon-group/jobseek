package recruitee

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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
	case json.Number:
		number, err := v.Float64()
		return err == nil && number != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}

func salaryUnit(value any) string {
	raw, ok := value.(string)
	if !ok {
		return "year"
	}
	key := strings.ToLower(strings.TrimSpace(raw))
	for _, entry := range []struct{ needle, unit string }{
		{"two_weeks", "week"}, {"biweekly", "week"},
		{"hour", "hour"}, {"month", "month"}, {"week", "week"},
		{"year", "year"}, {"annual", "year"}, {"daily", "day"}, {"day", "day"},
	} {
		if strings.Contains(key, entry.needle) {
			return entry.unit
		}
	}
	switch key {
	case "hr", "per-hour-wage":
		return "hour"
	case "mo":
		return "month"
	case "yr":
		return "year"
	}
	return "year"
}

func optionalText(value any, field string) (string, error) {
	if !truthy(value) {
		return "", nil
	}
	text, ok := value.(string)
	if !ok {
		return "", fmt.Errorf("Recruitee %s is not text", field)
	}
	return text, nil
}

func parseLocations(offer map[string]any) ([]string, error) {
	result := []string{}
	seen := map[string]bool{}
	if value, present := offer["locations"]; present {
		locations, ok := value.([]any)
		if !ok {
			return nil, errors.New("Recruitee locations must be a list")
		}
		for _, raw := range locations {
			location, ok := raw.(map[string]any)
			if !ok {
				return nil, errors.New("Recruitee location must be an object")
			}
			city, err := optionalText(location["city"], "location city")
			if err != nil {
				return nil, err
			}
			country, err := optionalText(location["country"], "location country")
			if err != nil {
				return nil, err
			}
			parts := []string{}
			for _, value := range []string{city, country} {
				if value != "" {
					parts = append(parts, value)
				}
			}
			name := strings.Join(parts, ", ")
			if name != "" && !seen[name] {
				result = append(result, name)
				seen[name] = true
			}
		}
	}
	if len(result) == 0 {
		if value, ok := offer["location"].(string); ok && value != "" {
			result = append(result, value)
		}
	}
	if len(result) == 0 {
		return nil, nil
	}
	return result, nil
}

func parseSalary(offer map[string]any) (map[string]any, error) {
	value := offer["salary"]
	if !truthy(value) {
		return nil, nil
	}
	salary, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("Recruitee salary must be an object")
	}
	if salary["min"] == nil && salary["max"] == nil {
		return nil, nil
	}
	return map[string]any{
		"currency": salary["currency"],
		"min":      salary["min"],
		"max":      salary["max"],
		"unit":     salaryUnit(salary["period"]),
	}, nil
}

func parseJob(offer map[string]any) (Job, bool, error) {
	if truthy(offer["careers_url"]) {
		if _, ok := offer["careers_url"].(string); !ok {
			return Job{}, false, errors.New("Recruitee careers_url must be text")
		}
	}
	url, ok := offer["careers_url"].(string)
	if !ok || url == "" {
		return Job{}, false, nil
	}
	locations, err := parseLocations(offer)
	if err != nil {
		return Job{}, false, err
	}
	salary, err := parseSalary(offer)
	if err != nil {
		return Job{}, false, err
	}
	parts := []string{}
	for _, field := range []string{"description", "requirements"} {
		if truthy(offer[field]) {
			value, ok := offer[field].(string)
			if !ok {
				return Job{}, false, fmt.Errorf("Recruitee %s must be text", field)
			}
			parts = append(parts, value)
		}
	}
	var description any
	if len(parts) > 0 {
		description = strings.Join(parts, "\n")
	}
	var employment any
	if truthy(offer["employment_type_code"]) {
		employment = offer["employment_type_code"]
	}
	var locationType *string
	for _, pair := range []struct{ field, value string }{{"remote", "remote"}, {"hybrid", "hybrid"}, {"on_site", "onsite"}} {
		if truthy(offer[pair.field]) {
			value := pair.value
			locationType = &value
			break
		}
	}
	metadata := map[string]any{}
	for _, pair := range []struct{ source, target string }{
		{"department", "department"}, {"category_code", "category"}, {"id", "id"},
	} {
		if truthy(offer[pair.source]) {
			metadata[pair.target] = offer[pair.source]
		}
	}
	if value, ok := offer["tags"].([]any); ok && len(value) > 0 {
		metadata["tags"] = value
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	return Job{
		URL: url, Title: offer["title"], Description: description, Locations: locations,
		EmploymentType: employment, JobLocationType: locationType,
		DatePosted: offer["published_at"], BaseSalary: salary, Metadata: metadata,
	}, true, nil
}

// Parse extracts the same published rich offer fields as the configured
// Recruitee Python monitor. Malformed required input never becomes an empty
// successful inventory.
func Parse(body []byte) (Inventory, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var root map[string]any
	if err := decoder.Decode(&root); err != nil || root == nil {
		return Inventory{}, errors.New("Recruitee response must be a JSON object")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Inventory{}, errors.New("Recruitee response has trailing JSON")
	}
	rawOffers, exists := root["offers"]
	if !exists {
		rawOffers = []any{}
	}
	offers, ok := rawOffers.([]any)
	if !ok {
		return Inventory{}, errors.New("Recruitee offers must be a list")
	}
	result := Inventory{Jobs: make([]Job, 0, len(offers))}
	for index, raw := range offers {
		offer, ok := raw.(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Recruitee offer %d must be an object", index)
		}
		if offer["status"] != "published" {
			continue
		}
		job, present, err := parseJob(offer)
		if err != nil {
			return Inventory{}, fmt.Errorf("Recruitee offer %d: %w", index, err)
		}
		if present {
			result.Jobs = append(result.Jobs, job)
		}
	}
	result.Truncated = len(result.Jobs) > MaxJobs
	return result, nil
}
