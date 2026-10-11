package queue

import (
	"testing"
)

func TestDOMMonitorRetainsScraperRetryHintWithoutChangingListingAttempts(t *testing.T) {
	var previous string
	for _, hint := range []string{"0", "2", "5", "true"} {
		c := domMonitorConfig()
		c["metadata"] = `{"render":false,"link_selector":"a.job","retry_statuses":{"503":` + hint + `},"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.Profile != "dom.direct-urls/v1" || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) {
			t.Fatal("scraper hint changed monitor route", e)
		}
		o, e := DOMMonitorOptions(c)
		if e != nil || o.Attempts != 3 || len(o.Document.RetryLimits) != 0 {
			t.Fatal("scraper hint changed original listing attempt budget", e)
		}
		if previous == p.EffectiveConfigSHA256 {
			t.Fatal("ignored hint lost immutable config binding")
		}
		previous = p.EffectiveConfigSHA256
	}
	for _, hint := range []string{`true`, `{"503":6}`, `{"503":-1}`, `{"200":2}`, `{"503":1,"503":2}`} {
		c := domMonitorConfig()
		c["metadata"] = `{"retry_statuses":` + hint + `}`
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("unproved hint shape admitted", hint)
		}
	}
}

func TestAPIMonitorRetainsUnusedPaginationBrowserHintAndRejectsActualBrowserRoute(t *testing.T) {
	var previous string
	for _, hint := range []string{"false", "true"} {
		c := profileConfig()
		c["crawler_type"], c["domain"], c["throttle_key"] = "api_sniffer", "api_sniffer", "api_sniffer"
		c["board_url"] = "https://example.com/careers"
		c["metadata"] = `{"api_url":"https://example.com/api/jobs?page=1","json_path":"jobs","url_field":"url","fields":{"title":"name"},"pagination":{"param_name":"page","start_value":1,"browser":` + hint + `},"scraper_type":"skip"}`
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.Profile != "api_sniffer.http-items/v1" || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) {
			t.Fatal("annotation changed HTTP route", e)
		}
		if !APISnifferMonitorResourceMatches(p, c, "https://example.com/api/jobs?page=2") || APISnifferMonitorResourceMatches(p, c, "https://foreign.example/api/jobs?page=2") || APISnifferMonitorResourceMatches(p, c, "https://example.com/admin?page=2") {
			t.Fatal("annotation changed endpoint authority")
		}
		if previous == p.EffectiveConfigSHA256 {
			t.Fatal("pagination annotation lost original hash binding")
		}
		previous = p.EffectiveConfigSHA256
		c["metadata"] = `{"api_url":"https://example.com/api/jobs?page=1","json_path":"jobs","url_field":"url","fields":{"title":"name"},"pagination":{"param_name":"page","browser":` + hint + `},"browser":true}`
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("actual browser request acquired HTTP ownership")
		}
	}
	for _, hint := range []string{`1`, `"true"`, `null`} {
		c := profileConfig()
		c["crawler_type"] = "api_sniffer"
		c["metadata"] = `{"api_url":"https://example.com/api/jobs","json_path":"jobs","url_field":"url","pagination":{"param_name":"page","browser":` + hint + `}}`
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("unproved pagination hint admitted")
		}
	}
}
