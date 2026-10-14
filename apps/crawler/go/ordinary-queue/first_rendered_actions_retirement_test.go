package queue

import (
	"encoding/json"
	"testing"
)

func TestRealRenderedActionsColdRetirementConservesAttempts(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			testDetailFixtureRetirement(t, func(t *testing.T) firstOwnerFixture {
				options := map[string]any{"render": true, "enrich": []string{"description"}, "actions": []any{map[string]any{"action": "wait", "ms": 0}, map[string]any{"action": "click", "selector": "#details", "required": true}}}
				if scraper == "dom" {
					options["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "article", "field": "description"}}
				}
				if scraper == "nextdata" {
					options["path"] = "job"
					options["fields"] = map[string]any{"title": "title", "description": "description"}
				}
				metadata, _ := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": options})
				return firstIndependentDetailFixture(t, string(metadata), "https://careers.example.net/job/123", "careers.example.net", Browser)
			})
		})
	}
}
