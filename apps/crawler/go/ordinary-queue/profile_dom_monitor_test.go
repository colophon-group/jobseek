package queue

import "testing"

func domMonitorConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "dom", "dom", "dom"
	c["board_url"] = "https://careers.example.com/listing?locale=en"
	c["scraper_needs_browser"] = "1"
	c["metadata"] = `{"render":false,"url_filter":{"include":"/jobs/\\w+","exclude":"intern"},"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}]}}`
	return c
}

func TestDOMMonitorBindsStaticInventoryAndSeparateBrowserDetails(t *testing.T) {
	c := domMonitorConfig()
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "dom.direct-urls/v1" || p.Endpoint != c["board_url"] || p.Provider != "dom" {
		t.Fatal("static monitor with separate browser details rejected", p, err)
	}
	c["metadata"] = `{"scraper_config":{"steps":[{"field":"title","tag":"h1"}],"render":true},"scraper_type":"dom","url_filter":{"exclude":"intern","include":"/jobs/\\w+"},"render":false}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("nested key order changed ownership binding", err)
	}
	c["metadata"] = `{"scraper_type":"skip","url_filter":"/roles/"}`
	q, err = InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("changed inventory retained ownership", err)
	}
}

func TestDOMMonitorRejectsUnsupportedRoutesBeforeOwnership(t *testing.T) {
	for _, md := range []string{
		`{"render":true}`, `{"actions":[{"action":"click"}]}`, `{"pagination":{"selector":"a.next"}}`,
		`{"proxy":"enabled"}`, `{"skip_ssl":"enabled"}`, `{"ssl_verify":false}`, `{"transport_attempts":6}`,
		`{"transport_attempts":true}`, `{"link_selector":"["}`, `{"encoding":"shift_jis"}`,
		`{"request_headers":{"Authorization":"private"}}`, `{"request_headers":{"Accept":"a"," accept ":"b"}}`,
		`{"url_filter":{"include":42}}`, `{"url_filter":{"unknown":"x"}}`, `{"unknown":true}`,
		`{"render":true,"render":false}`, `{"url_filter":{"include":"a","include":"b"}}`,
	} {
		c := domMonitorConfig()
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported DOM inventory admitted", md)
		}
	}
	for _, endpoint := range []string{"http://careers.example.com/jobs", "https://u:p@careers.example.com/jobs", "https://careers.example.com:443/jobs", "https://careers.example.com/jobs#x"} {
		c := domMonitorConfig()
		c["board_url"] = endpoint
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsafe monitor endpoint admitted", endpoint)
		}
	}
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("browser monitor claimed as static")
	}
}
