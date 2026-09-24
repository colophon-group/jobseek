package greenhouse

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

const MaxJobs = 50_000

var strayToggle = regexp.MustCompile(`(?is)<a([^>]*)>\s*(?:read|learn|show|see|view)\s+more\s*</a>`)
var hrefAttribute = regexp.MustCompile(`(?i)\shref=`)

// Job contains precisely the rich fields the configured Greenhouse monitor
// passes to the existing Python writer. The writer still owns persistence.
type Job struct {
	URL         string         `json:"url"`
	Title       *string        `json:"title"`
	Description *string        `json:"description"`
	Locations   []string       `json:"locations"`
	DatePosted  any            `json:"date_posted"`
	Language    any            `json:"language"`
	Metadata    map[string]any `json:"metadata"`
}

type Inventory struct {
	Jobs      []Job `json:"jobs"`
	Truncated bool  `json:"truncated"`
}

func cleanText(value any) *string {
	text, ok := value.(string)
	if !ok {
		return nil
	}
	cleaned := strings.Join(strings.Fields(text), " ")
	if cleaned == "" {
		return nil
	}
	return &cleaned
}

func cleanDescription(value any) *string {
	text, ok := value.(string)
	if !ok {
		return nil
	}
	if text == "" {
		return &text
	}
	cleaned := strayToggle.ReplaceAllStringFunc(text, func(anchor string) string {
		openingEnd := strings.IndexByte(anchor, '>')
		if openingEnd < 0 || hrefAttribute.MatchString(anchor[:openingEnd]) {
			return anchor
		}
		return ""
	})
	return &cleaned
}

func namedObjects(raw map[string]any, field string) ([]any, error) {
	value, present := raw[field]
	if !present {
		return nil, nil
	}
	items, ok := value.([]any)
	if !ok {
		return nil, fmt.Errorf("Greenhouse %s must be a list", field)
	}
	return items, nil
}

func parseJob(raw map[string]any, index int) (Job, error) {
	url, ok := raw["absolute_url"].(string)
	if !ok || url == "" {
		return Job{}, fmt.Errorf("Greenhouse job at index %d has no absolute_url", index)
	}
	job := Job{
		URL:         url,
		Title:       cleanText(raw["title"]),
		Description: cleanDescription(raw["content"]),
		DatePosted:  raw["first_published"],
		Language:    raw["language"],
	}
	seen := make(map[string]bool)
	addLocation := func(value any) {
		name := cleanText(value)
		if name != nil && !seen[*name] {
			job.Locations = append(job.Locations, *name)
			seen[*name] = true
		}
	}
	if location, ok := raw["location"].(map[string]any); ok {
		addLocation(location["name"])
	}
	offices, err := namedObjects(raw, "offices")
	if err != nil {
		return Job{}, err
	}
	for _, item := range offices {
		object, ok := item.(map[string]any)
		if !ok {
			return Job{}, errors.New("Greenhouse office must be an object")
		}
		addLocation(object["name"])
	}
	metadata := make(map[string]any)
	departments, err := namedObjects(raw, "departments")
	if err != nil {
		return Job{}, err
	}
	names := make([]any, 0, len(departments))
	for _, item := range departments {
		object, ok := item.(map[string]any)
		if !ok {
			return Job{}, errors.New("Greenhouse department must be an object")
		}
		if name := object["name"]; truthy(name) {
			names = append(names, name)
		}
	}
	if len(names) > 0 {
		metadata["departments"] = names
	}
	for _, field := range []string{"education", "requisition_id"} {
		if value := raw[field]; truthy(value) {
			metadata[field] = value
		}
	}
	if len(metadata) > 0 {
		job.Metadata = metadata
	}
	return job, nil
}

func truthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case string:
		return typed != ""
	case bool:
		return typed
	case float64:
		return typed != 0
	case []any:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	default:
		return true
	}
}

func Parse(body []byte) (Inventory, error) {
	var root map[string]any
	if err := json.Unmarshal(body, &root); err != nil || root == nil {
		return Inventory{}, errors.New("Greenhouse jobs response must be a JSON object")
	}
	rawJobs, ok := root["jobs"].([]any)
	if !ok {
		return Inventory{}, errors.New("Greenhouse jobs response must contain a jobs list")
	}
	result := Inventory{Jobs: make([]Job, 0, len(rawJobs)), Truncated: len(rawJobs) > MaxJobs}
	for index, value := range rawJobs {
		raw, ok := value.(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Greenhouse job at index %d must be an object", index)
		}
		job, err := parseJob(raw, index)
		if err != nil {
			return Inventory{}, err
		}
		result.Jobs = append(result.Jobs, job)
	}
	return result, nil
}
