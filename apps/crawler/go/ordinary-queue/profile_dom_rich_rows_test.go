package queue

import (
	"strings"
	"testing"
)

func TestDOMRichRowsBindTransportEnrichmentAndReplacementOrder(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		t.Run(route, func(t *testing.T) {
			c := domMonitorConfig()
			c["scraper_needs_browser"] = "0"
			c["metadata"] = `{"rich_rows":{"row_selector":"article","link_selector":"a","title_replacements":{"Senior":"Lead","Lead":"Staff"}},"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
			want := "dom.direct-rows/v1"
			if route == "proxy" {
				c["metadata"] = strings.Replace(c["metadata"], `{"rich_rows":`, `{"proxy":true,"rich_rows":`, 1)
				want = "dom.proxy-rows/v1"
			}
			if route == "rendered" {
				c["metadata"] = strings.Replace(c["metadata"], `{"rich_rows":`, `{"render":true,"rich_rows":`, 1)
				c["monitor_needs_browser"] = "1"
				want = "dom.rendered-rows/v1"
			}
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.Profile != want || !DOMMonitorUsesRichRows(p.Profile) || (MonitorWorker(p) == Browser) != (route == "rendered") {
				t.Fatal("rich-row transport/profile mismatch", p, err)
			}
			fields, err := DOMRichMonitorEnrichment(c)
			if err != nil || len(fields) != 1 || fields[0] != "description" {
				t.Fatal("detail delegation lost", fields, err)
			}
			c["metadata"] = strings.Replace(c["metadata"], `"Senior":"Lead","Lead":"Staff"`, `"Lead":"Staff","Senior":"Lead"`, 1)
			q, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("changed replacement order retained ownership", err)
			}
		})
	}
}

func TestDOMRichRowsRefuseInvalidExtractionBeforeOwnership(t *testing.T) {
	for _, md := range []string{
		`{"rich_rows":{"row_selector":"article","title_replacements":{"a":"b","a":"c"}}}`,
		`{"rich_rows":{"row_selector":"article","total_selector":".total"},"pagination":{"param_name":"page"}}`,
		`{"rich_rows":{"row_selector":"article"},"scraper_type":"skip","scraper_config":{"enrich":["description"]}}`,
		`{"rich_rows":{"row_selector":"article"},"scraper_config":{"enrich":["unsupported"]}}`,
	} {
		c := domMonitorConfig()
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("invalid rich-row extraction admitted", md)
		}
	}
}
