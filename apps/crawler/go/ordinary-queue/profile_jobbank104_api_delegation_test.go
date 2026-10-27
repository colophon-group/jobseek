package queue

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestJobbank104HTTPAPIDetailDelegationRetainsListingAuthority(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["scraper_needs_browser"] = "jobbank104", "https://www.104.com.tw/company/abcde", "0"
		options := map[string]any{"api_url": "https://www.104.com.tw/job/ajax/content/{id}", "json_path": "data", "fields": map[string]any{"description": "description"}, "enrich": []string{"description"}}
		metadata := map[string]any{"proxy": proxy, "scraper_type": "api_sniffer", "scraper_config": options}
		bind := func() {
			body, err := json.Marshal(metadata)
			if err != nil {
				t.Fatal(err)
			}
			c["metadata"] = string(body)
		}
		bind()
		got, err := InspectRichMonitor(profileBoardID, c)
		want := "jobbank104.public-items/v1"
		if proxy {
			want = "jobbank104.proxy-public-items/v1"
		}
		if err != nil || got.Profile != want || MonitorWorker(got) != Simple || ProfileRequiresProxy(got.Profile) != proxy {
			t.Fatal("listing transport changed", err)
		}
		selected, err := smallProviderMonitorEnrichment(c)
		if err != nil || !reflect.DeepEqual(selected, []string{"description"}) {
			t.Fatal("description delegation changed", err)
		}
		routed, err := secondaryMonitorEnrichment(c)
		if err != nil || !reflect.DeepEqual(routed, selected) {
			t.Fatal("common writer lost description delegation", err)
		}
		detail, err := InspectHTTPAPIDetail(profileBoardID, c, "https://www.104.com.tw/job/abc123", Simple)
		if err != nil || detail.Profile != "api_sniffer.http-detail/v1" || !reflect.DeepEqual(detail.EnrichmentFields, selected) {
			t.Fatal("independent direct API detail changed", err)
		}
		metadata["scraper_type"] = "dom"
		metadata["scraper_config"] = map[string]any{"render": true, "steps": []any{map[string]any{"tag": "h1", "field": "title"}}}
		c["scraper_needs_browser"] = "1"
		bind()
		old, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || old.Profile != got.Profile || old.Endpoint != got.Endpoint || old.EffectiveConfigSHA256 == got.EffectiveConfigSHA256 {
			t.Fatal("detail change lost listing identity/config binding", err)
		}
		c["scraper_needs_browser"] = "0"
		metadata["scraper_type"] = "api_sniffer"
		metadata["scraper_config"] = options
		for _, fields := range [][]string{nil, {}, {"title"}, {"locations"}, {"description", "description"}} {
			options["enrich"] = fields
			bind()
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("unproved delegation mask admitted")
			}
		}
		options["enrich"] = []string{"description"}
		for _, key := range []string{"browser", "render", "actions", "unknown"} {
			options[key] = true
			bind()
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("unproved API option admitted", key)
			}
			delete(options, key)
		}
		c["scraper_needs_browser"] = "1"
		bind()
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("browser API detail entered HTTP delegation")
		}
		c["scraper_needs_browser"] = "0"
		metadata["scraper_type"] = "dom"
		metadata["scraper_config"] = map[string]any{"enrich": []string{"description"}}
		bind()
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unqualified legacy DOM delegation admitted")
		}
	}
}
