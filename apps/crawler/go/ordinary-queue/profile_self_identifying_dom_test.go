package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSelfIdentifyingDOMSourceKeepsOriginalOwnershipBinding(t *testing.T) {
	raw, err := os.ReadFile("testdata/self_identifying_cma_metadata.json")
	if err != nil {
		t.Fatal(err)
	}
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"], c["scraper_needs_browser"] = "dom", "1", "1"
	c["board_url"], c["metadata"] = "https://www.airbelgium.com/jobs/", string(raw)
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != domRenderedMonitorProfile || MonitorWorker(p) != Browser {
		t.Fatal("source-bound original profile refused", err)
	}
	_, nav, err := RenderedDOMMonitorOptions(c)
	if err != nil || nav["stealth"] != nil {
		t.Fatal("self-identifying navigation not selected", err)
	}
	var md map[string]any
	if json.Unmarshal(raw, &md) != nil || md["stealth"] != true {
		t.Fatal("original configuration changed")
	}
	for _, key := range []string{"proxy", "persistent_context", "channel", "headless", "user_agent", "url_filter"} {
		t.Run(key, func(t *testing.T) {
			clone := cloneConfig(c)
			changed := map[string]any{}
			for k, v := range md {
				changed[k] = v
			}
			changed[key] = "unproved"
			body, _ := json.Marshal(changed)
			clone["metadata"] = string(body)
			if _, err := InspectRichMonitor(profileBoardID, clone); err == nil {
				t.Fatal("unqualified configuration acquired source authority")
			}
		})
	}
	foreign := cloneConfig(c)
	foreign["board_url"] = "https://foreign.example/jobs/"
	if _, err := InspectRichMonitor(profileBoardID, foreign); err == nil {
		t.Fatal("foreign board acquired source authority")
	}
}
