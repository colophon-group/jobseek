package queue

import (
	"encoding/json"
	"testing"
)

func TestSharedAPIAllowlistPreservesTransportAndCanonicalFence(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		t.Run(route, func(t *testing.T) {
			c := profileConfig()
			c["crawler_type"], c["board_url"] = "api_sniffer", "https://example.com/careers"
			md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "name"}, "scraper_type": "skip"}
			if route == "proxy" {
				md["proxy"] = true
			}
			if route == "rendered" {
				md["browser"] = true
				c["monitor_needs_browser"] = "1"
			}
			encode := func() { body, _ := json.Marshal(md); c["metadata"] = string(body) }
			encode()
			original, e := InspectRichMonitor(profileBoardID, c)
			if e != nil {
				t.Fatal(e)
			}
			md["url_allowlist"] = `^https://example\.com/jobs/[0-9]+$`
			encode()
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil || p.Profile != original.Profile || p.EffectiveConfigSHA256 == original.EffectiveConfigSHA256 {
				t.Fatal("allowlist transport or canonical identity lost", e)
			}
			rules, e := FeedMonitorURLRules(c)
			if e != nil {
				t.Fatal(e)
			}
			for raw, want := range map[string]bool{"https://example.com/jobs/1": true, "https://example.com/jobs/1/admin": false, "https://other.example/jobs/1": false} {
				got, e := rules.ProviderAllows(raw)
				if e != nil || got != want {
					t.Fatal("whole-provider boundary lost", raw, got, e)
				}
			}
			for _, bad := range []any{nil, true, "", "["} {
				md["url_allowlist"] = bad
				encode()
				if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
					t.Fatal("invalid boundary admitted before claim", bad)
				}
			}
		})
	}
}
func TestDOMGroupedSelectorPreservesDirectProxyRenderedProfiles(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		c := domMonitorConfig()
		md := map[string]any{"link_selector": "h3 a[href], h4 a[href]", "url_filter": "/jobs/", "scraper_type": "json-ld"}
		if route == "proxy" {
			md["proxy"] = true
		}
		if route == "rendered" {
			md["render"] = true
			c["monitor_needs_browser"] = "1"
		}
		body, _ := json.Marshal(md)
		c["metadata"] = string(body)
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || ProfileRequiresProxy(p.Profile) != (route == "proxy") || (MonitorWorker(p) == Browser) != (route == "rendered") {
			t.Fatal("grouped selector transport lost", route, e)
		}
		md["link_selector"] = "h3 a[href], h5 a[href]"
		body, _ = json.Marshal(md)
		c["metadata"] = string(body)
		changed, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.EffectiveConfigSHA256 == changed.EffectiveConfigSHA256 {
			t.Fatal("selector group escaped canonical fence", e)
		}
	}
}
