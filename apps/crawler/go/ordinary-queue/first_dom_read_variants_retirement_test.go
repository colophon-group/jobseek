package queue

import (
	"encoding/json"
	"testing"
)

func TestRealDOMGroupedReadVariantsColdRetirement(t *testing.T) {
	for _, kind := range []string{"alternate", "linked", "combined"} {
		t.Run(kind, func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				config := map[string]any{"steps": []any{map[string]any{"tag": "p", "field": "description", "html": true}}}
				if kind != "linked" {
					config["fetch_url_transform"] = map[string]any{"find": "^https://example.com/", "replace": "https://read.example.net/"}
				}
				if kind != "alternate" {
					config["linked_description"] = map[string]any{"selector": "a.detail", "allowed_host_suffixes": []string{".example.net"}, "steps": []any{map[string]any{"tag": "p", "field": "description", "html": true}}}
				}
				metadata, _ := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": config})
				return firstIndependentDetailFixtureAtBoard(t, string(metadata), "https://example.com/job/42", "example.com", "https://example.com/careers")
			})
		})
	}
}
