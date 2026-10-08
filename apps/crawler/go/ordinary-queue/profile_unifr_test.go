package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestUnifrCurrentRegistryFixedSourceBindings(t *testing.T) {
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
		if row[h["monitor_type"]] != "unifr" {
			continue
		}
		md := map[string]any{}
		if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
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
		c["crawler_type"], c["board_url"], c["metadata"] = "unifr", row[h["board_url"]], string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != "unifr.authoritative-items/v1" || p.Endpoint != c["board_url"] || MonitorWorker(p) != Simple || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		if SecondaryMonitorResourceMatches(p, c, "https://other.example/jobs") || SecondaryMonitorGone(c, p.Endpoint, 404, false) || SecondaryMonitorGone(c, p.Endpoint, 410, false) {
			t.Fatal("missing or foreign source admitted")
		}
		for _, change := range []string{"proxy", "source", "browser"} {
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
	if count != 9 {
		t.Fatal("registry coverage changed", count)
	}
}
