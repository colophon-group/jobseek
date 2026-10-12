package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestGroupedSelfIdentifyingDOMRetainsOriginalControlsAndRefusesDrift(t *testing.T) {
	cases := map[string]string{
		"alten_italy":           "https://www.alten.it/career/jobs/?per_page=100&sortby=date",
		"alten_netherlands":     "https://www.alten.nl/werken-bij-alten/career-nl/",
		"alten_switzerland":     "https://www.alten.ch/de/career/jobs/",
		"glasswall":             "https://careers.glasswall.com/",
		"alten_portugal":        "https://www.alten.pt/pt/career/jobs/",
		"alten_spain":           "https://www.alten.es/unete-a-alten/empleo/",
		"alten_finland":         "https://www.alten.fi/en/career/jobs/",
		"arcelormittal_ukraine": "https://ukraine.arcelormittal.com/people-and-careers/arcelormittal-kryvyi-rih-as-your-workplace",
	}
	for name, board := range cases {
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile("testdata/self_identifying_" + name + "_metadata.json")
			if err != nil {
				t.Fatal(err)
			}
			c := profileConfig()
			c["crawler_type"], c["monitor_needs_browser"], c["scraper_needs_browser"] = "dom", "1", "1"
			c["board_url"], c["metadata"] = board, string(raw)
			mdCheck, err := richProfileMetadata(c)
			if err != nil || !selfIdentifyingDOMSource(c, mdCheck) {
				t.Fatal("exact original static replacement binding refused", err)
			}
			if _, _, err := RenderedDOMMonitorOptions(c); err != nil {
				t.Fatal("rendered parser/navigation refused", err)
			}
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.Profile != domRenderedMonitorProfile || MonitorWorker(p) != Browser {
				t.Fatal("original grouped profile refused", err)
			}
			listing, nav, err := RenderedDOMMonitorOptions(c)
			if err != nil || !listing.RequireNonEmpty {
				t.Fatal("group lost nonempty replacement proof", err)
			}
			for _, key := range []string{"stealth", "proxy", "skip_ssl", "transport_attempts"} {
				if _, exists := nav[key]; exists {
					t.Fatal("direct replacement retained unsupported control", key)
				}
			}
			var md map[string]any
			if json.Unmarshal(raw, &md) != nil {
				t.Fatal("invalid original reference")
			}
			if name == "arcelormittal_ukraine" && md["skip_ssl"] != true || name != "arcelormittal_ukraine" && (md["proxy"] != true || md["stealth"] != true || md["transport_attempts"] != float64(3)) {
				t.Fatal("original controls absent from reference")
			}
			for _, key := range []string{"proxy", "skip_ssl", "stealth", "transport_attempts", "url_filter", "actions", "request_headers"} {
				t.Run(key, func(t *testing.T) {
					changed := map[string]any{}
					for k, v := range md {
						changed[k] = v
					}
					changed[key] = "unproved"
					body, _ := json.Marshal(changed)
					copy := cloneConfig(c)
					copy["metadata"] = string(body)
					if _, err := InspectRichMonitor(profileBoardID, copy); err == nil {
						t.Fatal("drift acquired replacement authority")
					}
				})
			}
			copy := cloneConfig(c)
			copy["board_url"] = "https://foreign.example/jobs/"
			if _, err := InspectRichMonitor(profileBoardID, copy); err == nil {
				t.Fatal("foreign source acquired replacement authority")
			}
		})
	}
}

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
