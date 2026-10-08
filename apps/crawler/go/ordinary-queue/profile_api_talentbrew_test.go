package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestAPIFiltersAndTalentBrewQualifyCurrentRegistryContracts(t *testing.T) {
	targets := map[string]bool{}
	for _, slug := range []string{"securitas-costa-rica", "planzer-careers", "ti-and-m-careers", "hsbc-germany", "united-nations-secretariat-careers", "axa-switzerland-careers", "swiss-football-association-sportjobs", "singapore-public-service-portal", "visana-main", "insel-gruppe-main", "zurich-airport-careers", "swiss-olympic-sportjobs", "helsana-careers", "kofi-annan-foundation-jobs", "alten-korea", "university-of-arkansas-system-careers-northark", "crh-careers-jura"} {
		targets[slug] = true
	}
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		slug, provider := row[head["board_slug"]], row[head["monitor_type"]]
		if !targets[slug] && provider != "talentbrew" {
			continue
		}
		md := map[string]any{}
		if raw := row[head["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("metadata")
		}
		md["scraper_type"] = row[head["scraper_type"]]
		if raw := row[head["scraper_config"]]; raw != "" {
			var v any
			if json.Unmarshal([]byte(raw), &v) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = v
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["board_url"], config["crawler_type"], config["metadata"] = row[head["board_url"]], provider, string(body)
		if md["browser"] == true || md["render"] == true {
			config["monitor_needs_browser"] = "1"
		}
		p, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || p.Provider != provider {
			t.Fatal("configured admission lost", slug, err)
		}
		counts[provider]++
		delete(targets, slug)
		if provider == "talentbrew" {
			if p.Profile != "talentbrew.listing-urls/v1" {
				t.Fatal("TalentBrew profile changed")
			}
			if _, err := inspectDetailOwnership(profileBoardID, config); err != nil {
				t.Fatal("configured TalentBrew detail binding rejected", slug, err)
			}
		}
	}
	if len(targets) != 0 || counts["talentbrew"] != 9 || counts["api_sniffer"] != 17 {
		t.Fatal("grouped configuration coverage changed", counts, targets)
	}
}
