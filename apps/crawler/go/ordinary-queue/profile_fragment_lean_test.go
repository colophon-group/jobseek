package queue

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestGroupedFragmentLeanDOMKeepsExactSourceAndNavigation(t *testing.T) {
	for _, name := range []string{"axis", "sulzer_schmid", "tyson_thailand"} {
		t.Run(name, func(t *testing.T) {
			var reference struct {
				URL      string `json:"board_url"`
				Browser  string `json:"scraper_needs_browser"`
				Metadata json.RawMessage
			}
			raw, err := os.ReadFile("testdata/fragment_lean_" + name + "_config.json")
			if err != nil || json.Unmarshal(raw, &reference) != nil {
				t.Fatal("reference unavailable", err)
			}
			c := profileConfig()
			c["crawler_type"], c["monitor_needs_browser"], c["scraper_needs_browser"] = "dom", "1", reference.Browser
			c["board_url"], c["metadata"] = reference.URL, string(reference.Metadata)
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.Profile != domRenderedMonitorProfile || p.Endpoint != reference.URL || MonitorWorker(p) != Browser {
				t.Fatal("exact navigation/profile refused", err)
			}
			listing, navigation, err := RenderedDOMMonitorOptions(c)
			if err != nil || !listing.RequireNonEmpty {
				t.Fatal("positive inventory proof missing", err)
			}
			if name == "tyson_thailand" && (navigation["resource_policy"] != "none" || !strings.Contains(c["metadata"], `"lean"`)) {
				t.Fatal("lean replacement lost original ownership binding")
			}
			clone := cloneConfig(c)
			clone["board_url"] += "-unproved"
			if _, err := InspectRichMonitor(profileBoardID, clone); err == nil {
				t.Fatal("unproved source acquired fragment/lean authority")
			}
			var metadata map[string]any
			if json.Unmarshal(reference.Metadata, &metadata) != nil {
				t.Fatal("invalid metadata")
			}
			metadata["url_filter"] = "/unproved/"
			body, _ := json.Marshal(metadata)
			clone = cloneConfig(c)
			clone["metadata"] = string(body)
			if _, err := InspectRichMonitor(profileBoardID, clone); err == nil {
				t.Fatal("changed metadata acquired source authority")
			}
		})
	}
}

func TestGenericDOMFragmentStillRequiresOwnedSource(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["board_url"] = "https://example.com/jobs#list"
	c["metadata"] = `{"render":true,"url_filter":"/jobs/","scraper_type":"skip"}`
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("generic fragment source admitted")
	}
}
