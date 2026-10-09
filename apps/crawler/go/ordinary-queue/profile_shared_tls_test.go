package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSharedHTTPMonitorTLSCurrentRegistryAndRetainedSitemapTransport(t *testing.T) {
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
	rendered := 0
	for _, row := range rows[1:] {
		md := map[string]any{}
		if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
			continue
		}
		if md["skip_ssl"] != true {
			continue
		}
		provider := row[h["monitor_type"]]
		if provider != "dom" && provider != "inline" && provider != "sitemap" {
			continue
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if v := row[h["scraper_config"]]; v != "" {
			var x any
			if json.Unmarshal([]byte(v), &x) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = x
		}
		// CSV sync explicitly preserves this original learned sitemap root.
		// Qualify its retained canonical state, not nonexistent CSV auto-discovery.
		if row[h["board_slug"]] == "pld-space-careers" {
			if md["sitemap_url"] != nil {
				t.Fatal("original CSV learned-root boundary changed")
			}
			md["sitemap_url"] = "https://www.pldspace.com/en/sitemap.xml"
		}
		raw, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(raw)
		if md["render"] == true || md["browser"] == true {
			c["monitor_needs_browser"] = "1"
		}
		p, e := InspectRichMonitor(profileBoardID, c)
		if c["monitor_needs_browser"] == "1" {
			if e == nil {
				t.Fatal("HTTP proof admitted browser exception")
			}
			rendered++
			continue
		}
		if e != nil {
			t.Fatal("current original TLS config rejected", row[h["board_slug"]], e)
		}
		skip, e := MonitorSkipsSSL(c)
		if e != nil || !skip || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) || p.SnapshotSHA256 != configDigest(c) {
			t.Fatal("transport authority not bound")
		}
		delete(md, "skip_ssl")
		other := cloneConfig(c)
		raw, _ = json.Marshal(md)
		other["metadata"] = string(raw)
		q, e := InspectRichMonitor(profileBoardID, other)
		if e != nil || q.Profile != p.Profile || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 || q.SnapshotSHA256 == p.SnapshotSHA256 {
			t.Fatal("TLS exception disappeared from immutable authority", e)
		}
		counts[provider]++
	}
	if counts["dom"] != 13 || counts["inline"] != 1 || counts["sitemap"] != 2 || rendered != 1 {
		t.Fatal("registry cohort changed", counts, rendered)
	}
}

func TestSharedHTTPMonitorTLSExceptionRetainsUnsupportedBoundaries(t *testing.T) {
	for _, provider := range []string{"dom", "inline", "sitemap"} {
		c := profileConfig()
		c["crawler_type"] = provider
		for _, raw := range []string{`{"skip_ssl":"true"}`, `{"skip_ssl":1}`, `{"skip_ssl":true,"skip_ssl":false}`, `{"skip_ssl":true,"proxy":true}`} {
			c["metadata"] = raw
			if _, e := MonitorSkipsSSL(c); e == nil {
				t.Fatal("unqualified TLS selector admitted", provider, raw)
			}
		}
		c["metadata"] = `{"skip_ssl":true}`
		c["monitor_needs_browser"] = "1"
		if _, e := MonitorSkipsSSL(c); e == nil {
			t.Fatal("browser exception admitted")
		}
	}
	c := profileConfig()
	c["metadata"] = `{"skip_ssl":true}`
	for _, provider := range []string{"api_sniffer", "rss", "workday"} {
		c["crawler_type"] = provider
		if _, e := MonitorSkipsSSL(c); e == nil {
			t.Fatal("unqualified provider exception admitted")
		}
	}
}
