package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestUmantisCurrentRegistryRoutesProxyAndDelegatedEnrichment(t *testing.T) {
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
	count, proxy, rich := 0, 0, 0
	for _, row := range rows[1:] {
		if row[h["monitor_type"]] != "umantis" {
			continue
		}
		md := map[string]any{}
		if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
			t.Fatal("monitor metadata")
		}
		md["scraper_type"] = row[h["scraper_type"]]
		var scraper any
		if json.Unmarshal([]byte(row[h["scraper_config"]]), &scraper) != nil {
			t.Fatal("scraper metadata")
		}
		md["scraper_config"] = scraper
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = "umantis", row[h["board_url"]], string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Provider != "umantis" || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || MonitorWorker(p) != Simple {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		if ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
			t.Fatal("transport authority differs", p)
		}
		if md["proxy"] == true {
			proxy++
		}
		fields, err := secondaryMonitorEnrichment(c)
		if err != nil {
			t.Fatal(err)
		}
		if len(fields) > 0 {
			rich++
		}
		count++
		c["monitor_needs_browser"] = "1"
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("browser downgraded")
		}
	}
	if count != 9 || proxy != 1 || rich != 3 {
		t.Fatal(count, proxy, rich)
	}
}
