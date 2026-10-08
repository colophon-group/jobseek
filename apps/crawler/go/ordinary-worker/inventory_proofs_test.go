package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"testing"
)

func TestDOMRequiredProofLinksRetainPythonURLIdentity(t *testing.T) {
	for _, test := range []struct{ href, pattern string }{
		{"/café", `https://example\.com/café`},
		{"/apply%broken", `https://example\.com/apply%broken`},
		{"?", `https://example\.com/careers\?lang=fr`},
	} {
		t.Run(test.href, func(t *testing.T) {
			metadata, _ := json.Marshal(map[string]any{"scraper_type": "skip", "link_selector": "a.job", "empty_states": []any{map[string]any{"selector": ".empty", "exact_text": "No jobs", "required_link_selector": "a.general", "required_link_url_pattern": test.pattern}}})
			config := map[string]string{"board_url": "https://example.com/careers?lang=fr#top", "crawler_type": "dom", "metadata": string(metadata), "monitor_needs_browser": "0"}
			options, err := queue.DOMMonitorOptions(config)
			if err != nil {
				t.Fatal(err)
			}
			body := `<div class="empty">No jobs</div><a class="general" href="` + test.href + `">Apply</a>`
			result, err := parseDOMInventory(context.Background(), RichDiscovery{}, queue.GreenhouseMonitorProfile{Endpoint: config["board_url"]}, options, body, config["board_url"], false)
			if err != nil || len(result.Jobs) != 0 {
				t.Fatal("required link lost Python URL identity", err)
			}
		})
	}
}
