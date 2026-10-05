package queue

import "testing"

func TestOracleMonitorBindsOptionsAndSeparateScraper(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "oracle_hcm", "oracle_hcm", "oracle_hcm"
	c["board_url"] = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
	c["metadata"] = `{"scraper_type":"oracle_hcm","scraper_config":{},"offset_overlap":10,"total_count_tolerance":20}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "oracle_hcm.finder-items/v1" || !OracleMonitorResourceMatches(p, c, p.Endpoint+",offset=190") {
		t.Fatal("Oracle monitor rejected", p, err)
	}
	c["metadata"] = `{"total_count_tolerance":20,"offset_overlap":10,"scraper_config":{},"scraper_type":"oracle_hcm"}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("JSON key order changed authority", err)
	}
	c["metadata"] = `{"scraper_type":"oracle_hcm","offset_overlap":20}`
	q, err = InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("changed finder option not bound", err)
	}
	for _, m := range []string{`{"host":"arbitrary.example.com","site":"CX_1"}`, `{"proxy":true}`, `{"offset_overlap":200}`, `{"fields":{"title":"Title"}}`, `{"unported":true}`, `{"scraper_config":{"enrich":["unported"]}}`, `{"scraper_type":"skip","scraper_config":{"enrich":["description"]}}`} {
		c["metadata"] = m
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported config admitted", m)
		}
	}
}

func TestOracleMonitorRetainsConfiguredAndAutomaticEnrichmentScheduling(t *testing.T) {
	for _, test := range []struct {
		metadata string
		fields   int
	}{
		{`{}`, 1},
		{`{"scraper_type":"oracle_hcm"}`, 0},
		{`{"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`, 1},
		{`{"scraper_config":{"enrich":["description","employment_type"]}}`, 2},
		{`{"scraper_config":{}}`, 0},
		{`{"scraper_config":null}`, 0},
	} {
		fields, err := oracleMonitorEnrichment(map[string]string{"metadata": test.metadata})
		if err != nil || len(fields) != test.fields {
			t.Fatal("Oracle detail delegation changed", test, fields, err)
		}
	}
}
