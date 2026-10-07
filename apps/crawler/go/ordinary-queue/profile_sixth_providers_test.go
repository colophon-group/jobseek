package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSixthProvidersCurrentRegistryCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil || len(rows) < 2 {
		t.Fatal("registry unavailable")
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if provider != "manatal" && provider != "hrmos" {
			continue
		}
		md := map[string]any{}
		raw := row[head["monitor_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid monitor metadata")
		}
		if scraper := row[head["scraper_type"]]; scraper != "" {
			md["scraper_type"] = scraper
		}
		if raw := row[head["scraper_config"]]; raw != "" {
			var sc any
			if json.Unmarshal([]byte(raw), &sc) != nil {
				t.Fatal("invalid detail metadata")
			}
			md["scraper_config"] = sc
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[head["board_url"]], string(body)
		p, e := InspectRichMonitor(profileBoardID, config)
		if e != nil || p.Provider != provider || MonitorWorker(p) != Simple {
			t.Fatal("provider unsupported", row[head["board_slug"]], e)
		}
		if !SecondaryMonitorResourceMatches(p, config, p.Endpoint) {
			t.Fatal("canonical resource unsupported")
		}
		counts[provider]++
	}
	if counts["manatal"] != 5 || counts["hrmos"] != 6 {
		t.Fatal("registry coverage changed", counts)
	}
	t.Logf("configuration eligibility only: %v", counts)
}
