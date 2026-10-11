package queue

import (
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"strings"
	"testing"
)

func TestBrowserAPIURLTransformRetainsCanonicalBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"] = "api_sniffer", "1"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"url_transform":{"find":"_GS_[A-Z_]+$","replace":""},"scraper_type":"skip"}`
	original := c["metadata"]
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != apiSnifferBrowserProfile || c["metadata"] != original {
		t.Fatal("browser URL policy lost original binding", p, err)
	}
	metadata, err := APISnifferBrowserReplayMetadata(c)
	if err != nil || strings.Contains(string(metadata), "url_transform") || c["metadata"] != original {
		t.Fatal("canonical policy entered network metadata or mutated source", err)
	}
	if _, err := api.BrowserReplayOptionsFromMetadata(c["board_url"], string(metadata)); err != nil {
		t.Fatal("renderer cannot parse the actual request metadata", err)
	}
	rules, err := FeedMonitorURLRules(c)
	if err != nil || rules.Rewrite("https://example.com/jobs/123_GS_PUBLIC") != "https://example.com/jobs/123" {
		t.Fatal("canonical transform changed", err)
	}
	clone := cloneConfig(c)
	clone["metadata"] = strings.Replace(original, `"replace":""`, `"replace":"suffix"`, 1)
	other, err := InspectRichMonitor(profileBoardID, clone)
	if err != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("transform omitted from authority binding", err)
	}
	for _, invalid := range []string{`"url_transform":{"find":"[","replace":""}`, `"url_transform":true`} {
		clone["metadata"] = strings.Replace(original, `"url_transform":{"find":"_GS_[A-Z_]+$","replace":""}`, invalid, 1)
		if _, err := APISnifferBrowserMonitorOptions(clone); err == nil {
			t.Fatal("malformed canonical policy admitted")
		}
	}
	if APISnifferMonitorResourceMatches(p, c, "https://other.example/api") {
		t.Fatal("URL transform granted foreign fetch authority")
	}
}
