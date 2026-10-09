package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSharedInventoryOptionsCurrentRegistryBinding(t *testing.T) {
	expected := map[string]bool{"adidas-korea-retail": true, "arcelormittal-south-africa": true, "bae-systems-prismatic": true, "carrefour-france-careers-fr-match": true, "cox-careers-eu": true, "nexthop-ai-careers": true, "roboa-careers": true, "salesforce-careers": true, "securitas-france": true, "sinopec-careers-ca": true, "softbank-career": true, "u-blox-careers": true, "vertiv-data-racks": true, "vertiv-data-racks-general": true}
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
	for _, row := range rows[1:] {
		slug := row[h["board_slug"]]
		if !expected[slug] {
			continue
		}
		t.Run(slug, func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
				t.Fatal("metadata")
			}
			md["scraper_type"] = row[h["scraper_type"]]
			if raw := row[h["scraper_config"]]; raw != "" {
				var v any
				if json.Unmarshal([]byte(raw), &v) != nil {
					t.Fatal("scraper metadata")
				}
				md["scraper_config"] = v
			}
			raw, _ := json.Marshal(md)
			c := profileConfig()
			c["crawler_type"], c["board_url"], c["metadata"] = row[h["monitor_type"]], row[h["board_url"]], string(raw)
			if md["render"] == true || md["browser"] == true {
				c["monitor_needs_browser"] = "1"
			}
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil {
				t.Fatal("original config rejected", e)
			}
			if (MonitorWorker(p) == Browser) != (c["monitor_needs_browser"] == "1") || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) || p.SnapshotSHA256 != configDigest(c) {
				t.Fatal("transport/binding changed")
			}
			if c["crawler_type"] == "dom" {
				options, e := DOMMonitorOptions(c)
				if e != nil || options.FetchURL == "" || !DOMMonitorResourceMatches(p, c, options.FetchURL) || !DOMMonitorPrimaryResourceMatches(p, c, options.FetchURL) || DOMMonitorResourceMatches(p, c, options.FetchURL+"/unbound") || DOMMonitorPrimaryResourceMatches(p, c, p.Endpoint) {
					t.Fatal("alternate fetch identity not sealed", e)
				}
			} else {
				delete(md, "enrich")
				delete(md, "slug_fields")
				without, _ := json.Marshal(md)
				other := cloneConfig(c)
				other["metadata"] = string(without)
				q, e := InspectRichMonitor(profileBoardID, other)
				if e != nil || p.EffectiveConfigSHA256 == q.EffectiveConfigSHA256 || p.SnapshotSHA256 == q.SnapshotSHA256 {
					t.Fatal("new option missing from binding", e)
				}
			}
		})
		delete(expected, slug)
	}
	if len(expected) != 0 {
		t.Fatal("missing registry boards", expected)
	}
}

func TestSharedInventoryOptionsRetainUnsupportedGuards(t *testing.T) {
	for _, extra := range []string{`"fetch_url_transform":{"find":"nomatch","replace":"https://example.com/alt"}`, `"fetch_url_transform":{"find":"https","replace":"https://127.0.0.1/"}`, `"fetch_url_transform":{"find":"^https://example.com","replace":"https://example.net/"},"pagination":{"param_name":"page"}`, `"fetch_url_transform":{"find":"^https://example.com","replace":"https://example.net/","unknown":true}`} {
		c := profileConfig()
		c["crawler_type"] = "dom"
		c["metadata"] = `{"scraper_type":"json-ld",` + extra + `}`
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("invalid alternate read admitted", extra)
		}
	}
}
