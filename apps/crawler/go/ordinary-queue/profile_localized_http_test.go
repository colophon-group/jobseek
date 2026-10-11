package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestLocalizedHTTPCanonicalRegistry(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	head := map[string]int{}
	for i, k := range rows[0] {
		head[k] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if !LocalizedHTTPProvider(provider) {
			continue
		}
		t.Run(row[head["board_slug"]], func(t *testing.T) {
			md := map[string]any{}
			raw := row[head["monitor_config"]]
			if strings.TrimSpace(raw) == "" {
				raw = "{}"
			}
			if json.Unmarshal([]byte(raw), &md) != nil {
				t.Fatal("registry metadata")
			}
			md["scraper_type"] = row[head["scraper_type"]]
			var detail any
			raw = row[head["scraper_config"]]
			if strings.TrimSpace(raw) == "" {
				raw = "null"
			}
			if json.Unmarshal([]byte(raw), &detail) != nil {
				t.Fatal("registry scraper metadata")
			}
			md["scraper_config"] = detail
			body, _ := json.Marshal(md)
			config := profileConfig()
			config["board_slug"] = row[head["board_slug"]]
			config["crawler_type"] = provider
			config["board_url"] = row[head["board_url"]]
			config["metadata"] = string(body)
			p, e := InspectRichMonitor(profileBoardID, config)
			if e != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
				t.Fatal("canonical profile differs", e)
			}
			if !SecondaryMonitorResourceMatches(p, config, p.Endpoint) || SecondaryMonitorResourceMatches(p, config, "https://foreign.example/jobs") {
				t.Fatal("resource authority escaped")
			}
			counts[provider]++
			for _, key := range []string{"render", "skip_ssl", "browser_session", "channel", "persistent_context"} {
				md[key] = true
				changed, _ := json.Marshal(md)
				bad := cloneConfig(config)
				bad["metadata"] = string(changed)
				if _, e := InspectRichMonitor(profileBoardID, bad); e == nil {
					t.Fatal("unqualified transport admitted", key)
				}
				delete(md, key)
			}
			if provider != "talemetry" {
				assertSQLOnlySkipDetailOwnership(t, config)
			}
		})
	}
	if counts["talemetry"] != 2 || counts["kipt"] != 1 || counts["prospective"] != 1 {
		t.Fatalf("canonical group incomplete %v", counts)
	}
}
