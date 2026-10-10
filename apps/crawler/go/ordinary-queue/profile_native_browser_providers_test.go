package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestPairedNativeBrowserConfiguredRouting(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	columns := map[string]int{}
	for i, name := range rows[0] {
		columns[name] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[columns["monitor_type"]]
		if provider != "accenture" && provider != "brassring" {
			continue
		}
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["monitor_needs_browser"] = provider, row[columns["board_url"]], "1"
		var metadata map[string]any
		if json.Unmarshal([]byte(row[columns["monitor_config"]]), &metadata) != nil {
			t.Fatal("configured metadata invalid")
		}
		metadata["scraper_type"] = "skip"
		body, _ := json.Marshal(metadata)
		config["metadata"] = string(body)
		profile, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || MonitorWorker(profile) != Browser || !NativeBrowserMonitorResourceMatches(profile, config, profile.Endpoint) || !initialMonitorResourceMatches(profile, profile.Endpoint) {
			t.Fatal("paired configuration lost original browser queue", row[columns["board_slug"]], err)
		}
		counts[profile.Profile]++
		if NativeBrowserMonitorResourceMatches(profile, config, "https://evil.test/api") || initialMonitorResourceMatches(profile, "https://evil.test/api") {
			t.Fatal("foreign resource admitted")
		}
		metadata["proxy"] = true
		body, _ = json.Marshal(metadata)
		config["metadata"] = string(body)
		if _, err = InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unsupported control silently removed")
		}
	}
	if !reflect.DeepEqual(counts, map[string]int{"accenture.http-items/v1": 12, "brassring.session-items/v1": 4}) {
		t.Fatal("paired census changed", counts)
	}
}

func TestNativeBrowserProviderConfigurationAndResourceBinding(t *testing.T) {
	for _, provider := range []string{"darwinbox", "bytedance", "brassring"} {
		config := profileConfig()
		config["crawler_type"] = provider
		config["monitor_needs_browser"] = "1"
		config["metadata"] = `{"scraper_type":"skip"}`
		config["board_url"] = "https://airtel.darwinbox.in/ms/candidate/careers"
		if provider == "bytedance" {
			config["board_url"] = "https://jobs.bytedance.com/experienced/position"
		} else if provider == "brassring" {
			config["board_url"] = "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
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
		if _, e := inspectDetailOwnership(profileBoardID, config); e == nil {
			t.Fatal("skip provider acquired detail ownership")
		}
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
