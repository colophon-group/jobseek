package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSharedHTTPProxyProfilesBindOriginalConfigAndIndependentDetails(t *testing.T) {
	for _, c := range []struct{ provider, metadata, profile string }{
		{"dom", `{"url_filter":"/jobs/","scraper_type":"json-ld"}`, "dom.proxy-urls/v1"},
		{"api_sniffer", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"json-ld"}`, "api_sniffer.proxy-http-items/v1"},
		{"inline", `{"steps":[{"tag":"h2","field":"title"}],"scraper_type":"skip"}`, "inline.proxy-document-items/v1"},
		{"sitemap", `{"sitemap_url":"https://example.com/jobs.xml","scraper_type":"json-ld"}`, "sitemap.proxy-explicit-urls/v1"},
		{"eightfold", `{"scraper_type":"eightfold"}`, "eightfold.proxy-pcsx-sitemap/v1"},
		{"phenom", `{"sitemap_url":"https://example.com/sitemap.xml","scraper_type":"json-ld"}`, "phenom.proxy-sitemap-urls/v1"},
	} {
		t.Run(c.provider, func(t *testing.T) {
			cfg := profileConfig()
			cfg["crawler_type"], cfg["board_url"], cfg["metadata"] = c.provider, "https://example.com/careers", c.metadata
			direct, e := InspectRichMonitor(profileBoardID, cfg)
			if e != nil {
				t.Fatal(e)
			}
			var md map[string]any
			_ = json.Unmarshal([]byte(c.metadata), &md)
			md["proxy"] = true
			body, _ := json.Marshal(md)
			cfg["metadata"] = string(body)
			original := cfg["metadata"]
			p, e := InspectRichMonitor(profileBoardID, cfg)
			if e != nil || p.Profile != c.profile || !ProfileRequiresProxy(p.Profile) || ProfileRequiresProxy(direct.Profile) || p.EffectiveConfigSHA256 == direct.EffectiveConfigSHA256 || p.SnapshotSHA256 == direct.SnapshotSHA256 || cfg["metadata"] != original {
				t.Fatal("transport escaped original binding", p, e)
			}
			if !initialMonitorResourceMatches(p, p.Endpoint) || initialMonitorResourceMatches(p, "https://foreign.example/unknown") {
				t.Fatal("compiled resource boundary lost")
			}
			if c.provider != "inline" {
				d, e := inspectDetailOwnership(profileBoardID, cfg)
				if e != nil || ProfileRequiresProxy(d.Profile) || d.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
					t.Fatal("independent detail changed transport or binding", e)
				}
			}
			cfg["monitor_needs_browser"] = "1"
			if _, e := InspectRichMonitor(profileBoardID, cfg); e == nil {
				t.Fatal("browser proxy admitted by HTTP proof")
			}
			cfg["monitor_needs_browser"] = "0"
			for _, override := range []string{`"proxy":true,"proxy":false`, `"proxy":"enabled"`, `"proxy":true,"ssl_verify":false`, `"proxy":true,"unknown":true`} {
				cfg["metadata"] = c.metadata[:len(c.metadata)-1] + "," + override + "}"
				if _, e := InspectRichMonitor(profileBoardID, cfg); e == nil {
					t.Fatal("unsupported proxy configuration admitted", override)
				}
			}
		})
	}
}

func TestSharedHTTPProxyCurrentRegistryCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	indexes := map[string]int{}
	for i, k := range rows[0] {
		indexes[k] = i
	}
	supported := map[string]bool{"dom": true, "api_sniffer": true, "inline": true, "sitemap": true, "eightfold": true, "phenom": true}
	monitors, details, pending := map[string]int{}, map[string]int{}, map[string]int{}
	for _, row := range rows[1:] {
		provider := row[indexes["monitor_type"]]
		if !supported[provider] {
			continue
		}
		md := map[string]any{}
		raw := row[indexes["monitor_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid registry metadata")
		}
		if md["proxy"] != true {
			continue
		}
		if v := row[indexes["scraper_type"]]; v != "" {
			md["scraper_type"] = v
		}
		if v := row[indexes["scraper_config"]]; v != "" {
			var value any
			if json.Unmarshal([]byte(v), &value) != nil {
				t.Fatal("invalid detail metadata")
			}
			md["scraper_config"] = value
		}
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[indexes["board_url"]], string(body)
		if md["render"] == true || md["browser"] == true {
			c["monitor_needs_browser"] = "1"
		}
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil {
			pending[provider]++
			continue
		}
		if !ProfileRequiresProxy(p.Profile) || MonitorWorker(p) != Simple {
			t.Fatal("proxy config acquired wrong transport")
		}
		monitors[provider]++
		if scraper, ok := md["scraper_type"].(string); ok && scraper != "skip" {
			if _, e := inspectDetailOwnership(profileBoardID, c); e == nil {
				details[provider]++
			}
		}
	}
	if monitors["eightfold"] != 9 || details["eightfold"] != 9 || monitors["inline"] != 2 || monitors["dom"] < 6 || monitors["sitemap"] < 3 {
		t.Fatal("expected configured HTTP coverage missing", monitors, details)
	}
	t.Logf("local eligibility only; monitors=%v details=%v remaining monitor variants=%v", monitors, details, pending)
}
