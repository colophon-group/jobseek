package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestSharedServiceAnnotationsCurrentRegistryBinding(t *testing.T) {
	expected := map[string]bool{"citadel-securities-global": true, "mutable-tactics-careers-ef": true, "uber-careers": true, "unitree-robotics-careers": true, "cyberbit-rangeforce-bamboohr": true, "molecubes-careers": true, "sophia-genetics-careers": true}
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
			// Removing an ignored extraction annotation must still change immutable authority.
			delete(md, "defaults")
			delete(md, "rescrape_policy")
			without, _ := json.Marshal(md)
			other := cloneConfig(c)
			other["metadata"] = string(without)
			q, e := InspectRichMonitor(profileBoardID, other)
			if e != nil || p.EffectiveConfigSHA256 == q.EffectiveConfigSHA256 || p.SnapshotSHA256 == q.SnapshotSHA256 || p.Profile != q.Profile {
				t.Fatal("annotation was stripped from authority", e)
			}
			switch c["crawler_type"] {
			case "dom":
				a, e := DOMMonitorOptions(c)
				b, z := DOMMonitorOptions(other)
				if e != nil || z != nil || !reflect.DeepEqual(a, b) {
					t.Fatal("DOM extraction changed", e, z)
				}
			case "sitemap":
				a, u, v, e := SitemapMonitorConfig(c)
				b, x, y, z := SitemapMonitorConfig(other)
				if e != nil || z != nil || !reflect.DeepEqual(a, b) || u != x || v != y {
					t.Fatal("sitemap extraction changed", e, z)
				}
			case "api_sniffer":
				if c["monitor_needs_browser"] == "1" {
					a, e := APISnifferBrowserMonitorOptions(c)
					b, z := APISnifferBrowserMonitorOptions(other)
					if e != nil || z != nil || !reflect.DeepEqual(a, b) {
						t.Fatal("browser replay changed", e, z)
					}
				} else {
					a, e := APISnifferMonitorOptions(c)
					b, z := APISnifferMonitorOptions(other)
					if e != nil || z != nil || !reflect.DeepEqual(a, b) {
						t.Fatal("API extraction changed", e, z)
					}
				}
			}
		})
		delete(expected, slug)
	}
	if len(expected) != 0 {
		t.Fatal("missing registry boards", expected)
	}
}

func TestSharedServiceAnnotationsRejectInvalidAndRetainOtherGuards(t *testing.T) {
	for _, provider := range []string{"dom", "api_sniffer", "sitemap"} {
		t.Run(provider, func(t *testing.T) {
			c := profileConfig()
			c["crawler_type"] = provider
			base := `{"scraper_type":"json-ld"}`
			if provider == "api_sniffer" {
				base = `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","scraper_type":"json-ld"}`
			}
			if provider == "sitemap" {
				base = `{"sitemap_url":"https://example.com/jobs.xml","scraper_type":"json-ld"}`
			}
			for _, extra := range []string{`"rescrape_policy":null`, `"rescrape_policy":true`, `"rescrape_policy":"always"`, `"rescrape_policy":"never","rescrape_policy":"never"`, `"defaults":null`, `"defaults":[]`, `"defaults":{"locations":[],"locations":[]}`, `"defaults":{},"unknown":true`, `"rescrape_policy":"never","skip_ssl":"enabled"`} {
				c["metadata"] = strings.TrimSuffix(base, "}") + "," + extra + "}"
				if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
					t.Fatal("unsupported configuration admitted", extra)
				}
			}
		})
	}
}
