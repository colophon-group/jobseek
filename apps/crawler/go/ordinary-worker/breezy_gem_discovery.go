package worker

import (
	"context"
	"encoding/json"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"strings"
	"time"
)

// Both existing providers make one public list request without pagination or
// provider retries. Discovery stays separate from canonical lifecycle authority.
func discoverBreezyGemInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	body, response, err := richPage(ctx, client, p.Endpoint, false)
	result := RichDiscovery{Jobs: []RichMonitorJob{}, Response: response}
	if err != nil {
		return result, err
	}
	jobs, truncated, err := parseBreezyGemInventory(body, p)
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", cause: err}
	}
	result.Jobs, result.Truncated = jobs, truncated
	return result, nil
}
func parseBreezyGemInventory(body []byte, p queue.GreenhouseMonitorProfile) ([]RichMonitorJob, bool, error) {
	var raw any
	d := json.NewDecoder(strings.NewReader(string(body)))
	d.UseNumber()
	if err := d.Decode(&raw); err != nil {
		return nil, false, err
	}
	if d.Decode(new(any)) != io.EOF {
		return nil, false, queue.ErrConfiguration
	}
	rows, ok := raw.([]any)
	if !ok {
		if p.Provider == "gem" {
			return []RichMonitorJob{}, false, nil
		}
		return nil, false, queue.ErrConfiguration
	}
	jobs := []RichMonitorJob{}
	count := 0
	for _, row := range rows {
		post, ok := row.(map[string]any)
		if !ok {
			if p.Provider == "breezy" {
				continue
			}
			return nil, false, queue.ErrConfiguration
		}
		if p.Provider == "breezy" {
			count++
			raw, _ := post["url"].(string)
			source := strings.TrimSpace(raw)
			if source != "" {
				var ok bool
				source, ok = joinPythonURL(strings.TrimSuffix(p.Endpoint, "/json")+"/", source)
				if !ok {
					return nil, false, queue.ErrConfiguration
				}
			} else if id, ok := post["friendly_id"].(string); ok && strings.TrimSpace(id) != "" {
				source = strings.TrimSuffix(p.Endpoint, "/json") + "/p/" + strings.TrimSpace(id)
			}
			if source != "" {
				jobs = append(jobs, RichMonitorJob{URL: source})
			}
			continue
		}
		if !gemFieldTruthy(post["absolute_url"]) {
			continue
		}
		source, ok := post["absolute_url"].(string)
		if !ok {
			if post["absolute_url"] == nil || post["absolute_url"] == "" {
				continue
			}
			return nil, false, queue.ErrConfiguration
		}
		if source == "" {
			continue
		}
		title, err := executor.CoerceText(post["title"])
		if err != nil {
			return nil, false, err
		}
		description, err := executor.CoerceText(post["content"])
		if err != nil {
			return nil, false, err
		}
		locations := []string{}
		seen := map[string]bool{}
		add := func(v any) error {
			if v == nil || v == "" {
				return nil
			}
			text, ok := v.(string)
			if !ok {
				return queue.ErrConfiguration
			}
			if !seen[text] {
				seen[text] = true
				locations = append(locations, text)
			}
			return nil
		}
		if offices, ok := post["offices"].([]any); ok {
			for _, raw := range offices {
				office, ok := raw.(map[string]any)
				if !ok {
					return nil, false, queue.ErrConfiguration
				}
				var name any
				if location, ok := office["location"].(map[string]any); ok {
					name = location["name"]
				}
				if name == nil || name == "" {
					name = office["name"]
				}
				if err := add(name); err != nil {
					return nil, false, err
				}
			}
		}
		if len(locations) == 0 {
			location := post["location"]
			if m, ok := location.(map[string]any); ok {
				location = m["name"]
			}
			if err := add(location); err != nil {
				return nil, false, err
			}
		}
		metadata := map[string]any{}
		if departments, ok := post["departments"].([]any); ok {
			names := []string{}
			for _, raw := range departments {
				if m, ok := raw.(map[string]any); ok {
					if name, ok := m["name"].(string); ok && name != "" {
						names = append(names, name)
					}
				}
			}
			if len(names) > 0 {
				metadata["department"] = strings.Join(names, ", ")
			}
		}
		if len(metadata) == 0 {
			metadata = nil
		}
		if len(locations) == 0 {
			locations = nil
		}
		var locationType any
		if text, ok := post["location_type"].(string); ok {
			if value := enrichment.NormalizeJobLocationType(text); value != "" {
				locationType = value
			}
		}
		employment := post["employment_type"]
		if !gemFieldTruthy(employment) {
			employment = nil
		}
		jobs = append(jobs, RichMonitorJob{URL: source, Title: title, Description: description, Locations: locations, DatePosted: post["first_published_at"], Metadata: metadata, EmploymentType: employment, JobLocationType: locationType})
	}
	if p.Provider == "gem" {
		count = len(jobs)
	}
	return jobs, count > 50_000, nil
}

// Gem's Python provider drops falsy JSON URLs and converts falsy employment
// fields to null before the existing central coercion boundary.
func gemFieldTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		n, _ := v.Float64()
		return n != 0
	case []any:
		return len(v) != 0
	case map[string]any:
		return len(v) != 0
	default:
		return true
	}
}
