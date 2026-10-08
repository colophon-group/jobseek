package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSeekAvatureCurrentRegistrySourceBindings(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, key := range rows[0] {
		h[key] = i
	}
	count := 0
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if provider != "seek" && provider != "avature" {
			continue
		}
		md := map[string]any{}
		if raw := row[h["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("monitor config")
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if raw := row[h["scraper_config"]]; raw != "" {
			var scraper any
			if json.Unmarshal([]byte(raw), &scraper) != nil {
				t.Fatal("detail config")
			}
			md["scraper_config"] = scraper
		}
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != map[string]string{"seek": "seek.advertiser-urls/v1", "avature": "avature.listing-urls/v1"}[provider] || MonitorWorker(p) != Simple || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		if SecondaryMonitorResourceMatches(p, c, "https://other.example/jobs") {
			t.Fatal("missing or foreign source admitted")
		}
		for _, change := range []string{"proxy", "unknown_option", "browser"} {
			bad := map[string]string{}
			for k, v := range c {
				bad[k] = v
			}
			if change == "browser" {
				bad["monitor_needs_browser"] = "1"
			} else {
				copyMD := map[string]any{}
				for k, v := range md {
					copyMD[k] = v
				}
				if change == "proxy" {
					copyMD[change] = true
				} else {
					copyMD[change] = "other"
				}
				encoded, _ := json.Marshal(copyMD)
				bad["metadata"] = string(encoded)
			}
			if _, err := InspectRichMonitor(profileBoardID, bad); err == nil {
				t.Fatal("unproven configuration admitted", change)
			}
		}
		count++
	}
	if count != 13 {
		t.Fatal("registry coverage changed", count)
	}
}
