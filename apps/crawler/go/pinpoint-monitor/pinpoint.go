package pinpoint

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

func text(value any, field string) (string, error) {
	if !truthy(value) {
		return "", nil
	}
	result, ok := value.(string)
	if !ok {
		return "", fmt.Errorf("Pinpoint %s is not text", field)
	}
	return result, nil
}

func description(posting map[string]any) (any, error) {
	parts := []string{}
	for _, pair := range []struct{ field, header string }{
		{"description", ""},
		{"key_responsibilities", "key_responsibilities_header"},
		{"skills_knowledge_expertise", "skills_knowledge_expertise_header"},
		{"benefits", "benefits_header"},
	} {
		body, err := text(posting[pair.field], pair.field)
		if err != nil {
			return nil, err
		}
		if body == "" {
			continue
		}
		if pair.header != "" {
			header, err := text(posting[pair.header], pair.header)
			if err != nil {
				return nil, err
			}
			if header != "" {
				body = "<h3>" + header + "</h3>\n" + body
			}
		}
		parts = append(parts, body)
	}
	if len(parts) == 0 {
		return nil, nil
	}
	return strings.Join(parts, "\n"), nil
}

func locations(posting map[string]any) ([]string, error) {
	raw := posting["location"]
	if !truthy(raw) {
		return nil, nil
	}
	location, ok := raw.(map[string]any)
	if !ok {
		return nil, errors.New("Pinpoint location is not an object")
	}
	name, err := text(location["name"], "location name")
	if err != nil {
		return nil, err
	}
	if name != "" {
		return []string{name}, nil
	}
	parts := []string{}
	for _, field := range []string{"city", "province"} {
		part, err := text(location[field], "location "+field)
		if err != nil {
			return nil, err
		}
		if part != "" {
			parts = append(parts, part)
		}
	}
	if len(parts) == 0 {
		return nil, nil
	}
	return []string{strings.Join(parts, ", ")}, nil
}

func salaryUnit(value any) string {
	raw, ok := value.(string)
	if !ok {
		return "year"
	}
	key := strings.ToLower(strings.TrimSpace(raw))
	for _, pair := range []struct{ needle, unit string }{
		{"two_weeks", "week"}, {"biweekly", "week"}, {"hour", "hour"},
		{"month", "month"}, {"week", "week"}, {"year", "year"},
		{"annual", "year"}, {"daily", "day"}, {"day", "day"},
	} {
		if strings.Contains(key, pair.needle) {
			return pair.unit
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

func salary(posting map[string]any) map[string]any {
	minimum, maximum := posting["compensation_minimum"], posting["compensation_maximum"]
	visible, present := posting["compensation_visible"]
	if (minimum == nil && maximum == nil) || (present && !truthy(visible)) {
		return nil
	}
	return map[string]any{
		"currency": posting["compensation_currency"],
		"min":      minimum, "max": maximum,
		"unit": salaryUnit(posting["compensation_frequency"]),
	}
}

// Keep these aliases aligned with src/core/enum_normalize.py until the
// shared Go normalization owner replaces the Python adapter.
var normalizedLocationTypes = map[string]string{
	"onsite":                      "onsite",
	"on-site":                     "onsite",
	"on-site working":             "onsite",
	"onsite working":              "onsite",
	"on_site":                     "onsite",
	"on site":                     "onsite",
	"office":                      "onsite",
	"in-office":                   "onsite",
	"in_office":                   "onsite",
	"in office":                   "onsite",
	"on-premises":                 "onsite",
	"in-person":                   "onsite",
	"in person":                   "onsite",
	"no":                          "onsite",
	"remote":                      "remote",
	"remote working":              "remote",
	"telecommute":                 "remote",
	"work from home":              "remote",
	"wfh":                         "remote",
	"fully remote":                "remote",
	"100% remote":                 "remote",
	"fulltime":                    "remote",
	"hybrid":                      "hybrid",
	"hybrid working":              "hybrid",
	"office, remote":              "hybrid",
	"remote, office":              "hybrid",
	"office/remote":               "hybrid",
	"remote/office":               "hybrid",
	"flexible":                    "hybrid",
	"partially remote":            "hybrid",
	"partial":                     "hybrid",
	"punctual":                    "hybrid",
	"remotework_none":             "onsite",
	"remotework_full":             "remote",
	"remotework_partial":          "hybrid",
	"vor ort":                     "onsite",
	"büro":                        "onsite",
	"homeoffice":                  "remote",
	"home office":                 "remote",
	"fernarbeit":                  "remote",
	"remote arbeit":               "remote",
	"teilweise remote":            "hybrid",
	"flexibel":                    "hybrid",
	"sur site":                    "onsite",
	"sur place":                   "onsite",
	"présentiel":                  "onsite",
	"en présentiel":               "onsite",
	"bureau":                      "onsite",
	"télétravail":                 "remote",
	"à distance":                  "remote",
	"travail à distance":          "remote",
	"hybride":                     "hybrid",
	"travail à distance possible": "hybrid",
	"télétravail partiel":         "hybrid",
	"gedeeltelijk afstandswerk":   "hybrid",
	"afstandswerk mogelijk":       "hybrid",
	"volledig afstandswerk":       "remote",
	"geen afstandswerk":           "onsite",
	"in sede":                     "onsite",
	"in ufficio":                  "onsite",
	"in loco":                     "onsite",
	"da remoto":                   "remote",
	"lavoro da remoto":            "remote",
	"telelavoro":                  "remote",
	"lavoro a distanza":           "remote",
	"ibrido":                      "hybrid",
}

func locationType(value any) *string {
	raw, ok := value.(string)
	if !ok {
		return nil
	}
	key := strings.ToLower(strings.TrimSpace(raw))
	result, found := normalizedLocationTypes[key]
	if !found && strings.HasSuffix(key, ")") {
		if base, _, qualified := strings.Cut(key, " ("); qualified {
			result, found = normalizedLocationTypes[base]
		}
	}
	if !found {
		return nil
	}
	return &result
}

func metadata(posting map[string]any) map[string]any {
	result := map[string]any{}
	job, ok := posting["job"].(map[string]any)
	if !ok {
		return nil
	}
	for _, pair := range []struct{ field, output string }{
		{"department", "department"}, {"division", "division"},
	} {
		object, ok := job[pair.field].(map[string]any)
		if ok && truthy(object["name"]) {
			result[pair.output] = object["name"]
		}
	}
	if truthy(job["requisition_id"]) {
		result["requisition_id"] = job["requisition_id"]
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

func parseJob(posting map[string]any) (Job, bool, error) {
	if !truthy(posting["url"]) {
		return Job{}, false, nil
	}
	url, ok := posting["url"].(string)
	if !ok {
		return Job{}, false, errors.New("Pinpoint posting URL is not text")
	}
	text, err := description(posting)
	if err != nil {
		return Job{}, false, err
	}
	place, err := locations(posting)
	if err != nil {
		return Job{}, false, err
	}
	employment := posting["employment_type"]
	if !truthy(employment) {
		employment = posting["employment_type_text"]
		if !truthy(employment) {
			employment = nil
		}
	}
	return Job{
		URL: url, Title: posting["title"], Description: text, Locations: place,
		EmploymentType: employment, JobLocationType: locationType(posting["workplace_type"]),
		DatePosted: posting["deadline_at"], BaseSalary: salary(posting), Metadata: metadata(posting),
	}, true, nil
}

// Parse extracts the published rich postings without accepting a partial
// response as a successful inventory.
func Parse(body []byte) (Inventory, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var root map[string]any
	if err := decoder.Decode(&root); err != nil || root == nil {
		return Inventory{}, errors.New("Pinpoint response must be a JSON object")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Inventory{}, errors.New("Pinpoint response has trailing JSON")
	}
	raw, exists := root["data"]
	if !exists {
		raw = []any{}
	}
	postings, ok := raw.([]any)
	if !ok {
		return Inventory{}, errors.New("Pinpoint data must be a list")
	}
	result := Inventory{Jobs: make([]Job, 0, len(postings))}
	for index, item := range postings {
		posting, ok := item.(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Pinpoint posting %d must be an object", index)
		}
		job, present, err := parseJob(posting)
		if err != nil {
			return Inventory{}, fmt.Errorf("Pinpoint posting %d: %w", index, err)
		}
		if present {
			result.Jobs = append(result.Jobs, job)
		}
	}
	result.Truncated = len(result.Jobs) > MaxJobs
	return result, nil
}
