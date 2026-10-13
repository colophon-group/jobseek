package queue

import "testing"

func TestNjoynURLMonitorPreservesDetailSchedulingAndTransportRefusal(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"], c["scraper_needs_browser"] = "njoyn", "1", "1"
	c["board_url"] = "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21001&page=joblisting"
	c["metadata"] = `{"scraper_type":"json-ld","page_wait_ms":0,"max_pages":100}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != "njoyn.session-urls/v1" || MonitorWorker(p) != Browser {
		t.Fatal("Njoyn owner/detail routing", e)
	}
	if !NativeBrowserMonitorResourceMatches(p, c, "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21001&page=JobDetails&jobid=1&brid=2") || NativeBrowserMonitorResourceMatches(p, c, "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21002&page=JobDetails&jobid=1&brid=2") {
		t.Fatal("tenant binding")
	}
	for _, metadata := range []string{`{"scraper_type":"json-ld","proxy":true}`, `{"scraper_type":"json-ld","stealth":true}`, `{"scraper_type":"json-ld","persistent_context":true}`, `{"scraper_type":"json-ld","headless":false}`, `{"scraper_type":"json-ld","channel":"chrome"}`, `{"scraper_type":"json-ld","actions":[]}`} {
		bad := cloneConfig(c)
		bad["metadata"] = metadata
		if _, e := InspectRichMonitor(profileBoardID, bad); e == nil {
			t.Fatal("unproved browser transport admitted")
		}
	}
}
