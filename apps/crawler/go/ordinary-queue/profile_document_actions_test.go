package queue

import (
	"strings"
	"testing"
)

func TestDOMAndInlineActionPipelinesBindFullCanonicalConfiguration(t *testing.T) {
	for _, provider := range []string{"dom", "inline"} {
		t.Run(provider, func(t *testing.T) {
			c := domMonitorConfig()
			c["crawler_type"], c["monitor_needs_browser"] = provider, "1"
			parser := `"url_filter":{"include":"/jobs/"},"scraper_type":"json-ld"`
			if provider == "inline" {
				parser = `"steps":[{"tag":"h2","field":"title"}],"scraper_type":"skip"`
			}
			c["metadata"] = `{"render":true,"actions":[{"action":"wait","ms":10},{"action":"evaluate","script":"() => window.ready = true","required":true}],` + parser + `}`
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || MonitorWorker(p) != Browser {
				t.Fatal("action parser not admitted", err)
			}
			c["metadata"] = strings.Replace(c["metadata"], "true}]", "false}]", 1)
			q, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.EffectiveConfigSHA256 == q.EffectiveConfigSHA256 {
				t.Fatal("failure policy absent from binding", err)
			}
			c["monitor_needs_browser"] = "0"
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("browser actions entered direct lane")
			}
		})
	}
	if validateRenderedNavigation(map[string]any{"render": true, "actions": []any{map[string]any{"action": "wait"}}}) == nil {
		t.Fatal("unqualified detail or feed actions admitted")
	}
}
