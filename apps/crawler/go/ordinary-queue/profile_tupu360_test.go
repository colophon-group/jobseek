package queue

import "testing"

func TestTupu360StaticRichRowsBindIndependentDetailsAndStrictRoot(t *testing.T) {
	c := domMonitorConfig()
	c["board_url"] = "https://fixture.tupu360.com/fixture/position/index"
	c["monitor_needs_browser"] = "0"
	c["metadata"] = `{"tupu360_listing":true,"scraper_type":"tupu360","scraper_config":{"enrich":["description"],"listing_enrichment":true}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "dom.direct-rows/v1" || !Tupu360Monitor(c) || !DOMMonitorUsesRichRows(p.Profile) {
		t.Fatal("static provider inventory not admitted", err)
	}
	if !DOMMonitorResourceMatches(p, c, c["board_url"]+"?max=100&offset=100") {
		t.Fatal("bounded offset rejected")
	}
	for _, suffix := range []string{"?offset=0&max=100&filter=x", "?offset=-1&max=100", "?offset=1&max=100&max=10", "?offset=1&max=101"} {
		if DOMMonitorResourceMatches(p, c, c["board_url"]+suffix) {
			t.Fatal("unqualified listing resource admitted")
		}
	}
	for _, md := range []string{`{"tupu360_listing":false}`, `{"tupu360_listing":true,"render":true}`, `{"tupu360_listing":true,"proxy":true}`, `{"tupu360_listing":true,"rich_rows":{}}`} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unqualified transport/parser admitted")
		}
	}
}
