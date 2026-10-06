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
			if e == nil {
				t.Fatal("configured proxy acquired direct monitor authority")
			}
			counts[provider+"_proxy_preserved"]++
			continue
		}
		if e != nil || profile.Provider != provider || MonitorWorker(profile) != Simple {
			t.Fatal("configured direct provider unsupported", row[headers["board_slug"]])
		}
		counts[provider]++
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
	if counts["adp"] != 14 || counts["cornerstone"] != 12 || counts["paylocity"] != 4 || counts["paylocity_proxy_preserved"] != 5 {
		t.Fatal("registry coverage fixture empty")
	}
	t.Logf("configuration eligibility only (no production authority): %v", counts)
}
