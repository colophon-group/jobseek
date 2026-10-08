package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestNotionCurrentRegistryMonitorAndCanonicalDetailBindings(t *testing.T) {
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
	count := 0
	for _, row := range rows[1:] {
		if row[h["monitor_type"]] != "notion" {
			continue
		}
		md := map[string]any{}
		if raw := row[h["monitor_config"]]; raw != "" {
			if json.Unmarshal([]byte(raw), &md) != nil {
				t.Fatal("metadata")
			}
		}
		md["scraper_type"] = row[h["scraper_type"]]
		var scraper any
		if raw := row[h["scraper_config"]]; raw != "" {
			if json.Unmarshal([]byte(raw), &scraper) != nil {
				t.Fatal("detail config")
			}
		}
		md["scraper_config"] = scraper
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = "notion", row[h["board_url"]], string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != "notion.public-urls/v1" || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || MonitorWorker(p) != Simple {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		d, err := inspectDetailOwnership(profileBoardID, c)
		if err != nil || d.Profile != "notion.public-detail/v1" || d.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
			t.Fatal("detail binding lost", row[h["board_slug"]], err, d)
		}
		source := "https://" + d.Domain + "/11111111111111111111111111111111"
		actual, err := InspectNotionDetail(profileBoardID, c, source, Simple)
		if err != nil || actual.EffectiveBoardSHA256 != d.EffectiveBoardSHA256 || actual.Endpoint != d.Endpoint {
			t.Fatal("posting route binding differs", err, actual)
		}
		if _, err := InspectNotionDetail(profileBoardID, c, "https://other.notion.site/11111111111111111111111111111111", Simple); err == nil {
			t.Fatal("foreign workspace detail admitted")
		}
		if _, err := InspectNotionDetail(profileBoardID, c, source, Browser); err == nil {
			t.Fatal("browser detail downgraded")
		}
		count++
	}
	if count != 8 {
		t.Fatal(count)
	}
}
