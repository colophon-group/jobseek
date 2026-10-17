package queue

import (
	"encoding/json"
	"testing"
)

func TestHeldDOMPaginationPreservesConfigAndOriginalParser(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"wait":"domcontentloaded","url_filter":"/jobs/","pagination":{"browser":true,"param_name":"pr","start":-1,"max_pages":100},"scraper_type":"json-ld"}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || MonitorWorker(p) != Browser || !DOMMonitorUsesHeldPagination(c) {
		t.Fatal("held profile unavailable", e)
	}
	listing, _, e := RenderedDOMMonitorOptions(c)
	if e != nil || listing.Pagination.Start != -1 || listing.Pagination.URL(c["board_url"], 2) != "https://careers.example.com/listing?locale=en&pr=0" {
		t.Fatal("original parser binding changed", e)
	}
	for _, extra := range []string{`"actions":[{"action":"wait"}]`, `"request_headers":{"x-test":"value"}`, `"stealth":true`, `"proxy":true`, `"user_agent":"changed"`} {
		var md map[string]json.RawMessage
		if json.Unmarshal([]byte(c["metadata"]), &md) != nil {
			t.Fatal("fixture")
		}
		var added map[string]json.RawMessage
		_ = json.Unmarshal([]byte("{"+extra+"}"), &added)
		for k, v := range added {
			md[k] = v
		}
		b, _ := json.Marshal(md)
		changed := cloneConfig(c)
		changed["metadata"] = string(b)
		if _, e := InspectRichMonitor(profileBoardID, changed); e == nil {
			t.Fatal("unsupported session controls admitted", extra)
		}
	}
}
