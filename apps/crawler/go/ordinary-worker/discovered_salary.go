package worker

import (
	"encoding/json"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

// DiscoveredJob.__post_init__ normalizes string salaries while retaining
// structured/non-string values. Reuse the already-ported salary parser;
// canonical rich columns continue to derive from the description.
func normalizeDiscoveredSalary(value any) (any, error) {
	text, ok := value.(string)
	if !ok {
		return value, nil
	}
	result, err := enrichment.Salary(text, nil)
	if err != nil {
		return nil, err
	}
	if result.Parsed == nil {
		return nil, nil
	}
	body, err := json.Marshal(result.Parsed)
	if err != nil {
		return nil, err
	}
	var salary map[string]any
	if err := json.Unmarshal(body, &salary); err != nil {
		return nil, err
	}
	return salary, nil
}
