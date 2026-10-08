package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestLinkedInTaleoPracticeMatchCurrentRegistryBindings(t *testing.T) {
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
	for i, k := range rows[0] {
		h[k] = i
	}
	counts := map[string]int{}
	profiles := map[string]string{"linkedin": "linkedin.guest-items/v1", "taleo": "taleo.listing-urls/v1", "practicematch": "practicematch.proxy-listing-urls/v1"}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		want, ok := profiles[provider]
		if !ok {
			continue
		}
		t.Run(row[h["board_slug"]], func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
				t.Fatal("metadata")
			}
			md["scraper_type"] = row[h["scraper_type"]]
			if raw := row[h["scraper_config"]]; raw != "" {
				var scraper any
				if json.Unmarshal([]byte(raw), &scraper) != nil {
					t.Fatal("scraper")
				}
				md["scraper_config"] = scraper
			}
			body, _ := json.Marshal(md)
			c := profileConfig()
			c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(body)
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.Profile != want || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || SecondaryMonitorResourceMatches(p, c, "https://foreign.example/private") {
				t.Fatal(p, err)
			}
			for _, key := range []string{"browser", "unknown_option", "proxy-string"} {
				bad := cloneConfig(c)
				copyMD := map[string]any{}
				for k, v := range md {
					copyMD[k] = v
				}
				if key == "browser" {
					bad["monitor_needs_browser"] = "1"
				} else if key == "proxy-string" {
					copyMD["proxy"] = "true"
				} else {
					copyMD[key] = true
				}
				b, _ := json.Marshal(copyMD)
				bad["metadata"] = string(b)
				if _, err := InspectRichMonitor(profileBoardID, bad); err == nil {
					t.Fatal("unproved option admitted", key)
				}
			}
		})
		counts[provider]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"linkedin": 31, "taleo": 4, "practicematch": 4}) {
		t.Fatal("registry changed", counts)
	}
}
