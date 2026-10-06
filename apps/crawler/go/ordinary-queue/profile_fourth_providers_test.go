package queue

import (
	"encoding/csv"
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"

	"os"
	"testing"
)

func TestFourthProvidersCurrentRegistryConfigurationCoverage(t *testing.T) {
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
		if provider != "paycom" && provider != "rippling" {
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
		case "paycom":
			o, err := api.PaycomOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectAPIDetail(profileBoardID, config, o.JobURL("123"), Simple)
		case "rippling":
			o, err := api.RipplingOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectAPIDetail(profileBoardID, config, o.JobURL("abc-123"), Simple)
		}

		if e != nil || detail.EffectiveBoardSHA256 != profile.EffectiveConfigSHA256 {
			t.Fatal("required detail binding unsupported", row[headers["board_slug"]], e)
		}
		counts[provider+"_detail"]++
	}
	if counts["paycom"] != 16 || counts["rippling"] != 10 {
		t.Fatal("registry coverage fixture empty")
	}
	t.Logf("configuration eligibility only (no production authority): %v", counts)
}
