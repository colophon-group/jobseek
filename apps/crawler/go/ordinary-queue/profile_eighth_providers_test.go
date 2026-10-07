package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestEighthProvidersCurrentRegistryCoverage(t *testing.T) {
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
		if provider != "earcu" && provider != "cvwarehouse" && provider != "woowa" {
			continue
		}
		md := map[string]any{}
		if raw := row[head["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid registry metadata")
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
			t.Fatal("existing provider configuration unsupported", provider, e)
		}
		if ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
			t.Fatal("proxy authority changed")
		}
		if !SecondaryMonitorResourceMatches(p, config, p.Endpoint) || SecondaryMonitorGone(config, p.Endpoint, 404, false) {
			t.Fatal("resource/gone contract differs")
		}
		if SecondaryMonitorResourceMatches(p, config, "https://example.com/other") {
			t.Fatal("foreign endpoint admitted")
		}
		config["monitor_needs_browser"] = "1"
		if _, e := InspectRichMonitor(profileBoardID, config); e == nil {
			t.Fatal("browser requirement ignored")
		}
		counts[provider]++
	}
	if counts["earcu"] != 3 || counts["cvwarehouse"] != 2 || counts["woowa"] != 3 {
		t.Fatal("registry coverage changed", counts)
	}
}
