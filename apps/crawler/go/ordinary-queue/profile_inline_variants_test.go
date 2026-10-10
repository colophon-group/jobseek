package queue

import (
	"encoding/json"
	"testing"
)

func TestStaticInlineDOMProfilesBindOriginalRoutesAndRichFields(t *testing.T) {
	for _, family := range []string{"onclick", "script-url", "script-rich"} {
		t.Run(family, func(t *testing.T) {
			c := domMonitorConfig()
			c["scraper_needs_browser"] = "0"
			m := map[string]any{"scraper_type": "json-ld"}
			if family == "onclick" {
				m["onclick_selector"] = "tr.item"
			} else {
				v := map[string]any{"variable": "jobs", "url_field": "link", "url_template": "{value}"}
				if family == "script-rich" {
					v["title_field"] = "title"
					v["locations_field"] = "locations"
					m["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				m["script_json_links"] = v
			}
			body, _ := json.Marshal(m)
			c["metadata"] = string(body)
			p, err := InspectRichMonitor(profileBoardID, c)
			want := "dom.direct-urls/v1"
			if family == "script-rich" {
				want = "dom.direct-rows/v1"
			}
			if err != nil || p.Profile != want {
				t.Fatal("original static profile rejected", err, p.Profile)
			}
			for _, key := range []string{"pagination", "fetch_url_transform", "render", "actions", "rich_rows"} {
				n := map[string]any{}
				for k, v := range m {
					n[k] = v
				}
				switch key {
				case "pagination":
					n[key] = map[string]any{"param_name": "page", "max_pages": 2}
				case "fetch_url_transform":
					n[key] = map[string]any{"find": "listing", "replace": "other"}
				case "render":
					n[key] = true
				case "actions":
					n[key] = []any{map[string]any{"action": "evaluate", "script": "() => true"}}
				case "rich_rows":
					n[key] = map[string]any{"row_selector": "article", "link_selector": "a"}
				}
				body, _ := json.Marshal(n)
				bad := cloneConfig(c)
				bad["metadata"] = string(body)
				if _, err := InspectRichMonitor(profileBoardID, bad); err == nil {
					t.Fatal("unsupported inline combination admitted", key)
				}
			}
			changed := cloneConfig(c)
			if family == "onclick" {
				m["onclick_selector"] = "tr.other"
			} else {
				m["script_json_links"].(map[string]any)["variable"] = "otherJobs"
			}
			body, _ = json.Marshal(m)
			changed["metadata"] = string(body)
			q, err := InspectRichMonitor(profileBoardID, changed)
			if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("changed static inventory retained authority", err)
			}
		})
	}
}
func TestRichRSSDescriptionEnrichmentRemainsExplicitAndFieldBound(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "rss"
	c["metadata"] = `{"preset":"generic","feed_url":"https://example.com/feed","scraper_type":"dom","scraper_config":{"enrich":["description"],"steps":[{"tag":"p","field":"description","html":true}]}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "rss.generic-items/v1" {
		t.Fatal("explicit description refresh rejected", err)
	}
	for _, field := range []string{"title", "locations", "employment_type"} {
		c["metadata"] = `{"preset":"generic","feed_url":"https://example.com/feed","scraper_type":"dom","scraper_config":{"enrich":["` + field + `"]}}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unqualified RSS field admitted", field)
		}
	}
}
