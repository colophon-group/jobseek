package queue

import (
	"strings"
	"testing"
)

func TestRenderedInlineBindsParserAndBrowserBoundary(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"], c["metadata"] = "inline", "1", `{"render":true,"wait":"domcontentloaded","timeout":15000,"steps":[{"tag":"h2","field":"title"}],"defaults":{"locations":["Zurich"]},"scraper_type":"skip"}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != inlineRenderedMonitorProfile || MonitorWorker(p) != Browser || !InlineMonitorResourceMatches(p, c, p.Endpoint) {
		t.Fatal(p, e)
	}
	changed := cloneConfig(c)
	changed["metadata"] = strings.Replace(c["metadata"], "Zurich", "Basel", 1)
	other, e := InspectRichMonitor(profileBoardID, changed)
	if e != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("defaults lost binding", e)
	}
	for _, field := range []string{`"proxy":true`, `"actions":[{"type":"click"}]`, `"stealth":true`, `"headless":false`, `"fetch_urls":["https://other.example/jobs"]`, `"fetch_json_path":"html"`, `"detail_click_selector":"a"`} {
		changed["metadata"] = strings.Replace(c["metadata"], `{"render":`, `{`+field+`,"render":`, 1)
		if _, e := InspectRichMonitor(profileBoardID, changed); e == nil {
			t.Fatal("unsupported route admitted", field)
		}
	}
	c["monitor_needs_browser"] = "0"
	if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
		t.Fatal("browser inventory entered simple lane")
	}
}
