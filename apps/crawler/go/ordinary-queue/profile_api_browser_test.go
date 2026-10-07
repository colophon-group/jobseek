package queue

import (
	"strings"
	"testing"
)

func TestBrowserAPIProfileBindsOriginalConfigurationAndBrowserLane(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"], c["monitor_needs_browser"] = "api_sniffer", "api_sniffer", "api_sniffer", "1"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"browser":true,"api_url":"https://example.com/api?page=1","json_path":"jobs","url_field":"url","fields":{"title":"title","description":"body"},"pagination":{"param_name":"page","start_value":1},"scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != apiSnifferBrowserProfile || MonitorWorker(p) != Browser || p.Endpoint != c["board_url"] {
		t.Fatal("browser API route changed", err, p.Profile)
	}
	for _, resource := range []string{p.Endpoint, "https://example.com/api?page=2"} {
		if !APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("bound resource rejected", resource)
		}
	}
	for _, resource := range []string{"https://other.example/api?page=2", "https://example.com/api?page=2&extra=1"} {
		if APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("foreign resource accepted")
		}
	}
	for _, extra := range []string{`,"proxy":true`, `,"actions":[]`, `,"channel":"chrome"`, `,"resource_policy":{}`, `,"api_url_match":"secret"`} {
		clone := cloneConfig(c)
		clone["metadata"] = strings.TrimSuffix(c["metadata"], "}") + extra + "}"
		if _, err := InspectRichMonitor(profileBoardID, clone); err == nil {
			t.Fatal("unported browser controls admitted", extra)
		}
	}
	c["monitor_needs_browser"] = "0"
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("browser API config claimed by simple worker")
	}
}
