package queue

import (
	"encoding/json"
	"strings"
	"testing"
)

const remainingRevolutMetadata = `{"render":true,"stealth":true,"wait":"domcontentloaded","path":"props.pageProps.positions","url_template":"https://www.revolut.com/careers/position/{slug}-{id}/","slug_fields":["text"],"fields":{"title":"text","locations":"locations[].name","metadata.team":"team"},"scraper_type":"nextdata","scraper_config":{"render":true,"path":"props.pageProps.position","fields":{"title":"text","locations":"locations[].name","description":"description"}}}`
const remainingIIHFMetadata = `{"render":true,"steps":[{"tag":"h3","attr":"class=s-sub-title","field":"title"},{"tag":"div","attr":"class=s-content","html":true,"field":"description","stop_tag":"h3"}],"defaults":{"locations":["Zurich, CH"]},"empty_selector":".s-content","empty_text":"Details of all future job opportunities will be advertised here","empty_requires_no_jobs":true,"fetch_urls":["https://canada-central.iihf.com/en/static/5082/jobs","https://eu-west.iihf.com/en/static/5082/jobs","https://www.iihf.com/en/static/5082/jobs"],"scraper_type":"skip"}`

func TestRemainingRenderedPresetsBindSourceAndOptions(t *testing.T) {
	for _, provider := range []string{"nextdata", "inline"} {
		t.Run(provider, func(t *testing.T) {
			c := profileConfig()
			c["crawler_type"], c["monitor_needs_browser"] = provider, "1"
			c["board_url"], c["metadata"] = "https://www.iihf.com/en/static/5082/jobs", remainingIIHFMetadata
			if provider == "nextdata" {
				c["board_url"], c["metadata"] = "https://www.revolut.com/careers/", remainingRevolutMetadata
			}
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil || MonitorWorker(p) != Browser {
				t.Fatal(p, e)
			}
			var md map[string]any
			if e = json.Unmarshal([]byte(c["metadata"]), &md); e != nil {
				t.Fatal(e)
			}
			mutate := func(key string, value any) {
				t.Helper()
				clone := cloneConfig(c)
				changed := map[string]any{}
				for k, v := range md {
					changed[k] = v
				}
				changed[key] = value
				b, _ := json.Marshal(changed)
				clone["metadata"] = string(b)
				if _, e := InspectRichMonitor(profileBoardID, clone); e == nil {
					t.Fatal("unproved option acquired authority", key)
				}
			}
			for key, value := range map[string]any{"proxy": true, "headless": false, "channel": "chrome", "persistent_context": true} {
				mutate(key, value)
			}
			foreign := cloneConfig(c)
			foreign["board_url"] = strings.Replace(c["board_url"], "www.", "foreign.", 1)
			if _, e := InspectRichMonitor(profileBoardID, foreign); e == nil {
				t.Fatal("foreign board acquired preset authority")
			}
			if provider == "nextdata" {
				o, nav, e := RenderedNextdataMonitorOptions(c)
				if e != nil || !o.Strict || o.BrowserDocumentTransform != "" || nav["stealth"] != nil {
					t.Fatal("source-bound Lightpanda contract changed", e)
				}
				mutate("path", "props.pageProps.unproved")
				mutate("fields", map[string]string{"title": "name"})
				mutate("url_template", "https://foreign.example/jobs/{id}")
			} else {
				mutate("fetch_urls", []string{"https://eu-west.iihf.com/en/static/5082/jobs", "https://canada-central.iihf.com/en/static/5082/jobs", c["board_url"]})
				mutate("fetch_urls", []string{"https://foreign.iihf.com/en/static/5082/jobs", "https://eu-west.iihf.com/en/static/5082/jobs", c["board_url"]})
				mutate("fetch_json_path", "html")
			}
			changed := cloneConfig(c)
			changed["metadata"] = strings.Replace(c["metadata"], "domcontentloaded", "load", 1)
			if provider == "inline" {
				changed["metadata"] = strings.Replace(c["metadata"], "Zurich, CH", "Basel, CH", 1)
			}
			other, e := InspectRichMonitor(profileBoardID, changed)
			if e != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("semantic binding lost", e)
			}
		})
	}
}
