package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestPortalHTTPProvidersCurrentRegistryAndTransportAuthority(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if !PortalHTTPProvider(provider) {
			continue
		}
		md := map[string]any{}
		raw := row[h["monitor_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("metadata")
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if raw = row[h["scraper_config"]]; raw != "" {
			var value any
			if json.Unmarshal([]byte(raw), &value) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = value
		}
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(body)
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || SecondaryMonitorResourceMatches(p, c, "https://foreign.example/private") {
			t.Fatal("registry binding lost", row[h["board_slug"]], e)
		}
		for _, bad := range []string{"{\"render\":true}", "{\"unknown\":true}", "{\"proxy\":\"true\"}"} {
			c["metadata"] = bad
			if _, e = InspectRichMonitor(profileBoardID, c); e == nil {
				t.Fatal("unsupported config accepted", provider)
			}
		}
		counts[provider]++
	}
	providers := []string{"pageup", "infoniqa", "keka", "turbohire"}
	if len(counts) != len(providers) {
		t.Fatal("registry provider families changed", counts)
	}
	for _, provider := range providers {
		if counts[provider] == 0 {
			t.Fatal("registry provider coverage missing", provider)
		}
	}
}
