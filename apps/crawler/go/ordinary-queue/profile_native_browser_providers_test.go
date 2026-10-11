package queue

import (
	"strings"
	"testing"
)

func TestNativeBrowserProviderConfigurationAndResourceBinding(t *testing.T) {
	for _, provider := range []string{"darwinbox", "bytedance"} {
		config := profileConfig()
		config["crawler_type"] = provider
		config["monitor_needs_browser"] = "1"
		config["metadata"] = `{"scraper_type":"skip"}`
		config["board_url"] = "https://airtel.darwinbox.in/ms/candidate/careers"
		if provider == "bytedance" {
			config["board_url"] = "https://jobs.bytedance.com/experienced/position"
		}
		p, e := InspectRichMonitor(profileBoardID, config)
		if e != nil || MonitorWorker(p) != Browser || !NativeBrowserProfile(p.Profile) {
			t.Fatal(provider, e)
		}
		if !NativeBrowserMonitorResourceMatches(p, config, p.Endpoint) || NativeBrowserMonitorResourceMatches(p, config, "https://evil.test/api") {
			t.Fatal("resource binding")
		}
		if provider == "darwinbox" {
			if !NativeBrowserMonitorResourceMatches(p, config, "https://airtel.darwinbox.in/ms/candidateapi/job/alljobs") {
				t.Fatal("bound jobs API")
			}
			if !SecondaryMonitorGone(config, "https://airtel.darwinbox.in/ms/candidateapi/job/alljobs", 410, false) || SecondaryMonitorGone(config, p.Endpoint, 410, false) {
				t.Fatal("first API gone contract")
			}
		}
		for _, metadata := range []string{`{"scraper_type":"skip","proxy":true}`, `{"scraper_type":"skip","actions":[{"click":"button"}]}`, `{"scraper_type":"skip","api_url":"https://evil.test"}`, `{"scraper_type":"dom"}`, `{"scraper_type":"skip","stealth":true}`} {
			bad := cloneConfig(config)
			bad["metadata"] = metadata
			if _, e := InspectRichMonitor(profileBoardID, bad); e == nil {
				t.Fatal("unsupported browser authority", metadata)
			}
		}
		bad := cloneConfig(config)
		bad["monitor_needs_browser"] = "0"
		if _, e := InspectRichMonitor(profileBoardID, bad); e == nil {
			t.Fatal("browser provider acquired direct owner")
		}
		assertSQLOnlySkipDetailOwnership(t, config)
		changed := cloneConfig(config)
		changed["metadata"] = `{"scraper_type":"skip","drop_threshold":0.2}`
		q, e := InspectRichMonitor(profileBoardID, changed)
		if e != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("lifecycle binding disappeared")
		}
		if len(p.EffectiveConfigSHA256) != 64 || strings.Contains(p.Profile, "proxy") {
			t.Fatal("profile identity")
		}
	}
}
