package queue

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

const profileBoardID = "00000000-0000-4000-8000-000000000001"

func profileConfig() map[string]string {
	return map[string]string{
		"board_slug": "fixture-careers", "board_url": "https://job-boards.greenhouse.io/fixture",
		"crawler_type": "greenhouse", "company_id": "00000000-0000-4000-8000-000000000002",
		"domain": "greenhouse", "throttle_key": "greenhouse",
		"monitor_needs_browser": "0", "scraper_needs_browser": "0",
		"check_interval_minutes": "60", "scrape_interval_hours": "24",
		"metadata": `{"token":"fixture","scraper_type":"skip"}`,
	}
}

func TestGreenhouseProfileBindsCanonicalBoardAndExactSettings(t *testing.T) {
	config := profileConfig()
	profile, err := InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil || profile.BoardID != profileBoardID || profile.Token != "fixture" || profile.Domain != "greenhouse" || profile.Endpoint != "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true" || profile.CheckInterval != time.Hour || profile.ScrapeInterval != 24*time.Hour || len(profile.EffectiveConfigSHA256) != 64 || profile.SnapshotSHA256 != configDigest(config) {
		t.Fatal("standard explicit rich profile was not retained")
	}
	for _, change := range []struct{ field, value string }{
		{"board_url", "https://job-boards.greenhouse.io/changed"},
		{"company_id", "00000000-0000-4000-8000-000000000003"},
		{"check_interval_minutes", "120"},
		{"scrape_interval_hours", "48"},
		{"metadata", `{"token":"changed","scraper_type":"skip"}`},
	} {
		changed := cloneConfig(config)
		changed[change.field] = change.value
		other, err := InspectGreenhouseMonitor(profileBoardID, changed)
		if err != nil || other.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256 || other.SnapshotSHA256 == profile.SnapshotSHA256 {
			t.Fatalf("effective %s change did not change both bindings", change.field)
		}
	}
	other, err := InspectGreenhouseMonitor("00000000-0000-4000-8000-000000000004", config)
	if err != nil || other.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256 {
		t.Fatal("configuration binding was reusable for another board")
	}
	config["metadata"] = `{"token":"caller-changed","scraper_type":"skip"}`
	if profile.Token != "fixture" {
		t.Fatal("inspection retained mutable caller state")
	}
}

func TestGreenhouseProfileRuntimeStateDoesNotRetireEffectiveConfig(t *testing.T) {
	config := profileConfig()
	before, err := InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil {
		t.Fatal(err)
	}
	config["metadata"] = `{"token":"fixture","scraper_type":"skip","suspect_streak":2,"recent_discovered_counts":[10,10,9],"_confirmed_drop_candidate":{"count":4}}`
	after, err := InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil || before.EffectiveConfigSHA256 != after.EffectiveConfigSHA256 || before.SnapshotSHA256 == after.SnapshotSHA256 {
		t.Fatal("runtime lifecycle update either retired profile or escaped exact snapshot binding")
	}
	config["egress_host"] = "boards-api.greenhouse.io"
	config["scrape_egress_host"] = "old-detail.example.invalid"
	learned, err := InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil || after.EffectiveConfigSHA256 != learned.EffectiveConfigSHA256 || after.SnapshotSHA256 == learned.SnapshotSHA256 {
		t.Fatal("learned egress attribution either retired profile or escaped snapshot binding")
	}
	// The CSV config fingerprint remains part of effective ownership, even when
	// runtime observations alone would otherwise look unchanged.
	config["metadata"] = `{"token":"fixture","scraper_type":"skip","_monitor_config_fingerprint":"changed"}`
	after, err = InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil || before.EffectiveConfigSHA256 == after.EffectiveConfigSHA256 {
		t.Fatal("source config generation escaped effective binding")
	}
}

func TestGreenhouseProfileRejectsUnsupportedBeforeSelection(t *testing.T) {
	for _, change := range []struct{ field, value string }{
		{"crawler_type", "lever"}, {"monitor_needs_browser", "1"}, {"scraper_needs_browser", "true"},
		{"company_id", "not-a-canonical-uuid"}, {"domain", "another-domain"},
		{"board_url", "http://job-boards.greenhouse.io/fixture"},
		{"board_url", "https://user:secret@job-boards.greenhouse.io/fixture"},
		{"board_url", "https://job-boards.greenhouse.io:443/fixture"},
		{"check_interval_minutes", "0"}, {"check_interval_minutes", "01"},
		{"check_interval_minutes", "+1"}, {"check_interval_minutes", "1.0"},
		{"scrape_interval_hours", "9223372036854775807"},
		{"metadata", "null"}, {"metadata", "[]"}, {"metadata", "{}"},
		{"metadata", `{"token":"fixture","scraper_type":"skip"} {}`},
		{"metadata", `{"token":"fixture","token":"another","scraper_type":"skip"}`},
		{"metadata", `{"token":1,"scraper_type":"skip"}`},
		{"metadata", `{"token":"../fixture","scraper_type":"skip"}`},
		{"metadata", `{"token":"fixture","scraper_type":"dom"}`},
		{"metadata", `{"token":"fixture","scraper_type":"skip","enrich":["description"]}`},
		{"metadata", `{"token":"fixture","scraper_type":"skip","proxy":"secret"}`},
		{"metadata", `{"token":"fixture","scraper_type":"skip","url_filter":"ignored"}`},
		{"metadata", `{"token":"fixture","scraper_type":"skip","scraper_config":{"fallback":{"type":"dom"}}}`},
		{"metadata", `{"token":"fixture","scraper_type":"skip","scraper_config":[]}`},
		{"unknown_runtime_setting", "secret"}, {"metadata", strings.Repeat("x", (1<<20)+1)},
		{"domain", strings.Repeat("x", 254)}, {"metadata", "\xff"},
	} {
		t.Run(change.field+"/"+change.value[:min(len(change.value), 32)], func(t *testing.T) {
			config := profileConfig()
			config[change.field] = change.value
			profile, err := InspectGreenhouseMonitor(profileBoardID, config)
			if !errors.Is(err, ErrUnsupportedProfile) || !reflect.DeepEqual(profile, GreenhouseMonitorProfile{}) || strings.Contains(err.Error(), "secret") {
				t.Fatal("unsupported configuration admitted a partial profile or leaked source text")
			}
		})
	}
}

func TestGreenhouseProfileEmptyScraperOptionsHaveNoExecutionEffect(t *testing.T) {
	for _, options := range []string{"null", "{}", "{ }"} {
		config := profileConfig()
		config["metadata"] = `{"token":"fixture","scraper_type":"skip","scraper_config":` + options + `}`
		if _, err := InspectGreenhouseMonitor(profileBoardID, config); err != nil {
			t.Fatal("empty skip options rejected")
		}
	}
}
