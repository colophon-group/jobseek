package queue

import (
	"encoding/csv"
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"

	"os"
	"testing"
)

func TestFifthProvidersCurrentDirectRegistryConfigurationCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil || len(rows) < 2 {
		t.Fatal("registry unavailable")
	}
	headers := map[string]int{}
	for i, key := range rows[0] {
		headers[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[headers["monitor_type"]]
		if provider != "adp" && provider != "cornerstone" && provider != "paylocity" {
			continue
		}
		metadata := map[string]any{}
		if raw := row[headers["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &metadata) != nil {
			t.Fatal("invalid provider configuration", row[headers["board_slug"]])
		}
		if scraper := row[headers["scraper_type"]]; scraper != "" {
			metadata["scraper_type"] = scraper
		}
		if scraper := row[headers["scraper_config"]]; scraper != "" {
			var value any
			if json.Unmarshal([]byte(scraper), &value) != nil {
				t.Fatal("invalid detail configuration")
			}
			metadata["scraper_config"] = value
		}
		encoded, e := json.Marshal(metadata)
		if e != nil {
			t.Fatal("configuration cannot be serialized")
		}
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[headers["board_url"]], string(encoded)
		profile, e := InspectRichMonitor(profileBoardID, config)
		if metadata["proxy"] == true {
			if e != nil || profile.Profile != "paylocity.proxy-embedded-items/v1" {
				t.Fatal("configured proxy lacks bound proxy monitor authority", e)
			}
			counts[provider+"_proxy"]++
		}
		if e != nil || profile.Provider != provider || MonitorWorker(profile) != Simple {
			t.Fatal("configured direct provider unsupported", row[headers["board_slug"]])
		}
		if metadata["proxy"] != true {
			counts[provider]++
		}
		scraper, _ := metadata["scraper_type"].(string)
		if scraper == "skip" {
			continue
		}
		var detail WorkdayDetailProfile
		switch provider {
		case "adp":
			o, err := api.ADPOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectAPIDetail(profileBoardID, config, o.JobURL("123_1"), Simple)
		case "paylocity":
			o, err := api.PaylocityOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			source := o.JobURL("123")
			if scraper == "json-ld" {
				detail, e = InspectJSONLDDetail(profileBoardID, config, source, Simple)
			} else {
				detail, e = InspectAPIDetail(profileBoardID, config, source, Simple)
			}
		}

		if e != nil || detail.EffectiveBoardSHA256 != profile.EffectiveConfigSHA256 {
			t.Fatal("required detail binding unsupported", row[headers["board_slug"]], e)
		}
		counts[provider+"_detail"]++
	}
	if counts["adp"] != 14 || counts["cornerstone"] != 12 || counts["paylocity"] != 4 || counts["paylocity_proxy"] != 5 {
		t.Fatal("registry coverage fixture empty")
	}
	t.Logf("configuration eligibility only (no production authority): %v", counts)
}

func TestADPIndependentScraperAssignmentsKeepExistingAPIMonitor(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	headers := map[string]int{}
	for i, key := range rows[0] {
		headers[key] = i
	}
	count := 0
	for _, row := range rows[1:] {
		if row[headers["scraper_type"]] != "adp" || row[headers["monitor_type"]] == "adp" {
			continue
		}
		var md map[string]any
		if json.Unmarshal([]byte(row[headers["monitor_config"]]), &md) != nil {
			t.Fatal("invalid existing monitor config")
		}
		var detail map[string]any
		if json.Unmarshal([]byte(row[headers["scraper_config"]]), &detail) != nil {
			t.Fatal("invalid ADP config")
		}
		md["scraper_type"], md["scraper_config"] = "adp", detail
		raw, e := json.Marshal(md)
		if e != nil {
			t.Fatal(e)
		}
		config := profileConfig()
		config["board_url"], config["crawler_type"], config["metadata"] = row[headers["board_url"]], row[headers["monitor_type"]], string(raw)
		board, e := api.ADPBoardFromURL(config["board_url"])
		if e != nil {
			t.Fatal("ADP listing identity lost", e)
		}
		route, e := InspectAPIDetail(profileBoardID, config, board.JobURL("123_1"), Simple)
		if e != nil || route.Profile != adpDetailProfile {
			t.Fatal("independent scraper unsupported", row[headers["board_slug"]], e)
		}
		monitor, e := InspectRichMonitor(profileBoardID, config)
		if e != nil || monitor.Profile != "api_sniffer.http-items/v1" || route.EffectiveBoardSHA256 != monitor.EffectiveConfigSHA256 {
			t.Fatal("existing API monitor changed", row[headers["board_slug"]], e)
		}
		count++
	}
	if count != 2 {
		t.Fatal("independent ADP assignments lost", count)
	}
}

func TestPaylocityDetailsKeepIndependentProxyConfiguration(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	headers := map[string]int{}
	for i, key := range rows[0] {
		headers[key] = i
	}
	direct, proxy := 0, 0
	for _, row := range rows[1:] {
		if row[headers["scraper_type"]] != "paylocity" {
			continue
		}
		md, detail := map[string]any{}, map[string]any{}
		if raw := row[headers["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid monitor config")
		}
		if raw := row[headers["scraper_config"]]; raw != "" && json.Unmarshal([]byte(raw), &detail) != nil {
			t.Fatal("invalid detail config")
		}
		md["scraper_type"], md["scraper_config"] = "paylocity", detail
		raw, _ := json.Marshal(md)
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = row[headers["monitor_type"]], row[headers["board_url"]], string(raw)
		// Source identity comes from the canonical vendor listing. Only the
		// scraper's own configuration chooses its transport in the legacy batch.
		o, e := api.PaylocityOptionsFromMetadata(config["board_url"], "{}")
		if e != nil {
			t.Fatal(e)
		}
		profile, e := InspectAPIDetail(profileBoardID, config, o.JobURL("123"), Simple)
		if detail["proxy"] == true {
			if e != nil || profile.Profile != paylocityProxyDetailProfile {
				t.Fatal("explicit proxy detail lacks bound proxy authority", e)
			}
			proxy++
			continue
		}
		if e != nil || profile.Profile != paylocityDetailProfile {
			t.Fatal("independent direct detail rejected", row[headers["board_slug"]], e)
		}
		direct++
	}
	if direct != 5 || proxy != 3 {
		t.Fatal("Paylocity transport assignments changed", direct, proxy)
	}
}
