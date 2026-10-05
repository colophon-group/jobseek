package queue

import "testing"

func TestConfiguredAPIProfileBindsFieldsRequestsAndDetailContract(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "api_sniffer", "api_sniffer", "api_sniffer"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"api_url":"https://example.com/api/jobs?page=1","json_path":"jobs","url_field":"url","fields":{"title":"name","description":"body"},"pagination":{"param_name":"page","start_value":1},"scraper_type":"json-ld","scraper_config":{"enrich":[]}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "api_sniffer.http-items/v1" {
		t.Fatalf("configured API rejected: %+v %v", p, err)
	}
	for _, resource := range []string{p.Endpoint, "https://example.com/api/jobs?page=2"} {
		if !APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("bound request rejected", resource)
		}
	}
	for _, resource := range []string{"https://other.example.com/api/jobs?page=2", "https://example.com/api/jobs?page=2&extra=x", "https://example.com/api/jobs?page=02"} {
		if APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("unbound request accepted", resource)
		}
	}
	c["metadata"] = `{"scraper_config":{"enrich":[]},"scraper_type":"json-ld","pagination":{"start_value":1,"param_name":"page"},"fields":{"description":"body","title":"name"},"url_field":"url","json_path":"jobs","api_url":"https://example.com/api/jobs?page=1"}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("JSON key order changed ownership", err)
	}
	c["metadata"] = `{"api_url":"https://example.com/api/jobs?page=1","json_path":"jobs","url_field":"url","fields":{"title":"different"}}`
	q, err = InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("field change not bound", err)
	}
	for _, metadata := range []string{`{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url"}`, `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"name"},"browser":true}`, `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"name"},"scraper_config":{"enrich":["description"]}}`} {
		c["metadata"] = metadata
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unported configuration admitted", metadata)
		}
	}
}
