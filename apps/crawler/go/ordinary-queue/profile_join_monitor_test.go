package queue

import "testing"

func joinMonitorConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "join", "join", "join"
	c["board_url"] = "https://join.com/companies/fixture"
	c["metadata"] = `{"scraper_type":"json-ld"}`
	return c
}

func TestJoinMonitorBindsCanonicalPaginationAndSeparateDetailOptions(t *testing.T) {
	c := joinMonitorConfig()
	c["scraper_needs_browser"] = "1"
	c["metadata"] = `{"slug":"fixture","scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "join.nextdata-urls/v1" || p.Token != "fixture" || p.Endpoint != c["board_url"] {
		t.Fatalf("existing direct Join board rejected: %+v %v", p, err)
	}
	for _, resource := range []string{p.Endpoint, p.Endpoint + "?page=2", p.Endpoint + "?page=10000"} {
		if !initialMonitorResourceMatches(p, resource) {
			t.Fatal("valid claimed board page excluded", resource)
		}
	}
	for _, resource := range []string{p.Endpoint + "?page=1", p.Endpoint + "?page=02", p.Endpoint + "?page=10001", p.Endpoint + "?page=2&page=3", p.Endpoint + "?page=2&extra=yes", p.Endpoint + "?page=%zz", p.Endpoint + "?page=2#fragment", "https://join.com/companies/foreign?page=2"} {
		if initialMonitorResourceMatches(p, resource) {
			t.Fatal("unbound page accepted", resource)
		}
	}
	c["metadata"] = `{"slug":"fixture","scraper_type":"json-ld"}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.EffectiveConfigSHA256 == q.EffectiveConfigSHA256 {
		t.Fatal("separate detail configuration not bound", err)
	}
	for _, metadata := range []string{`{"slug":"other"}`, `{"slug":"../fixture"}`, `{"slug":42}`, `{"proxy":"enabled"}`, `{"url_filter":{"include":"engineer"}}`, `{"drop_threshold":[]}`} {
		c["metadata"] = metadata
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported Join configuration admitted", metadata)
		}
	}
	c["metadata"] = `{"scraper_type":"json-ld"}`
	c["board_url"] = "https://join.com/companies/%66ixture"
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("encoded slug bypassed the canonical Python board route")
	}
}
