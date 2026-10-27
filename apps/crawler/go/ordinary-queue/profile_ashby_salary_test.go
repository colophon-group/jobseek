package queue

import "testing"

func TestAshbyJSONLDSalaryEnrichmentRetainsIndependentDetailBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "ashby"
	c["metadata"] = `{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":["base_salary"]}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Provider != "ashby" || p.Profile != "ashby.token-items/v1" || p.Endpoint != "https://api.ashbyhq.com/posting-api/job-board/fixture?includeCompensation=true" {
		t.Fatal("salary delegation changed sealed Ashby listing authority", err)
	}
	if _, err := inspectDetailOwnership(profileBoardID, c); err != nil {
		t.Fatal("independent JSON-LD detail assignment rejected", err)
	}
	without := cloneConfig(c)
	without["metadata"] = `{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":[]}}`
	q, err := InspectRichMonitor(profileBoardID, without)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("selected salary delegation lost immutable configuration binding", err)
	}
	for _, metadata := range []string{
		`{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`,
		`{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":["base_salary","description"]}}`,
		`{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":true}}`,
	} {
		invalid := cloneConfig(c)
		invalid["metadata"] = metadata
		if _, err := InspectRichMonitor(profileBoardID, invalid); err == nil {
			t.Fatal("unimplemented or malformed selected fields admitted")
		}
	}
}
