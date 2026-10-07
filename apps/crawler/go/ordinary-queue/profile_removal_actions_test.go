package queue

import (
	"encoding/json"
	"testing"
)

func TestDOMAndInlineRemovalPipelinesBindBrowserOwnership(t *testing.T) {
	for _, provider := range []string{"dom", "inline"} {
		t.Run(provider, func(t *testing.T) {
			c := domMonitorConfig()
			if provider == "inline" {
				c = profileConfig()
				c["crawler_type"] = "inline"
			}
			c["monitor_needs_browser"] = "1"
			metadata := map[string]any{"render": true, "scraper_type": "skip", "actions": []any{map[string]any{"action": "remove", "selector": ".obsolete", "required": true}, map[string]any{"action": "dismiss_overlays"}}}
			if provider == "inline" {
				metadata["steps"] = []any{map[string]any{"tag": "h2", "field": "title"}}
			} else {
				metadata["url_filter"] = map[string]any{"include": "/jobs/"}
			}
			body, _ := json.Marshal(metadata)
			c["metadata"] = string(body)
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || MonitorWorker(p) != Browser {
				t.Fatal("removal pipeline not bound to browser", err)
			}
			metadata["actions"].([]any)[0].(map[string]any)["selector"] = ".different"
			body, _ = json.Marshal(metadata)
			changed := cloneConfig(c)
			changed["metadata"] = string(body)
			q, err := InspectRichMonitor(profileBoardID, changed)
			if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("selector lost canonical identity", err)
			}
			changed["monitor_needs_browser"] = "0"
			if _, err := InspectRichMonitor(profileBoardID, changed); err == nil {
				t.Fatal("rendered pipeline entered simple lane")
			}
			metadata["proxy"] = true
			body, _ = json.Marshal(metadata)
			changed = cloneConfig(c)
			changed["metadata"] = string(body)
			if _, err := InspectRichMonitor(profileBoardID, changed); err == nil {
				t.Fatal("unsupported proxy admitted")
			}
		})
	}
}
