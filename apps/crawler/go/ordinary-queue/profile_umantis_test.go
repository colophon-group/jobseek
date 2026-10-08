package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestUmantisMigrationReceiptRemainsInEffectiveBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "umantis", "https://recruitingapp-3040.umantis.com/Jobs/All"
	c["metadata"] = `{"customer_id":"3040","scraper_type":"skip","_identity_migration_receipt":{"source_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state":"applied"}}`
	a, err := InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	c["metadata"] = `{"customer_id":"3040","scraper_type":"skip","_identity_migration_receipt":{"source_revision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","state":"applied"}}`
	b, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || a.Endpoint != b.Endpoint || a.EffectiveConfigSHA256 == b.EffectiveConfigSHA256 {
		t.Fatal("receipt lost from immutable configuration binding", err, a, b)
	}
}

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
		md["_identity_migration_receipt"] = map[string]any{"source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "provider": "umantis", "state": "applied"}
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
