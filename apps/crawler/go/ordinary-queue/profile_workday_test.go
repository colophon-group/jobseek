package queue

import (
	"encoding/json"
	"testing"
)

func TestWorkdayMonitorProfileBindsAllInventoryOptionsAndIndependentDetailFlags(t *testing.T) {
	config := profileConfig()
	config["crawler_type"] = "workday"
	config["board_url"] = "https://fixture.wd1.myworkdayjobs.com/en-US/Careers"
	config["metadata"] = `{"all_sites":false,"scraper_type":"workday","search_text":"Brand"}`
	config["scraper_needs_browser"] = "1"
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.Provider != "workday" || p.Profile != "workday.cxs-urls/v1" || p.Endpoint != "https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/Careers/jobs" {
		t.Fatalf("URL-only profile: %+v / %v", p, err)
	}
	config["metadata"] = `{"all_sites":false,"scraper_type":"workday","search_text":"Changed"}`
	changed, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("inventory filter escaped immutable binding", err)
	}
	config["metadata"] = `{"search_text":"Brand"}`
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("search over default multi-site admitted")
	}
	config["metadata"] = `{"ssl_verify":false}`
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("TLS verification override admitted")
	}
	config["metadata"] = `{}`
	config["monitor_needs_browser"] = "1"
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("browser monitor admitted to CXS HTTP profile")
	}
}

func TestWorkdayPythonDelistThresholdAndDropSettingSemantics(t *testing.T) {
	for _, tc := range []struct {
		raw  any
		want int
	}{{nil, 4}, {true, 4}, {json.Number("0.9"), 4}, {json.Number("4.7"), 4}, {json.Number("2"), 2}, {"3", 3}, {"3.5", 4}, {"bad", 4}, {json.Number("-2"), 4}} {
		got, err := workdayDelistThreshold(tc.raw)
		if err != nil || got != tc.want {
			t.Fatalf("threshold %v: %d / %v", tc.raw, got, err)
		}
	}
	for _, raw := range []any{json.Number("0"), "0", false} {
		if v, err := workdayLifecycleSetting(raw, 0.3); err != nil || v != 0 {
			t.Fatal("explicit zero drop setting lost", err)
		}
	}
}
