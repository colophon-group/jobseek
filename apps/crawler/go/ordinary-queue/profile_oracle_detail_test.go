package queue

import (
	"strings"
	"testing"
)

func TestOracleDetailBindsDefaultEnrichmentAndTrustedTenant(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "oracle_hcm"
	c["board_url"] = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
	source := "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123"
	for _, metadata := range []string{`{}`, `{"scraper_type":null,"scraper_config":null}`, `{"scraper_type":"","scraper_config":null}`, `{"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`, `{"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description","employment_type"]}}`} {
		c["metadata"] = metadata
		owned, err := inspectAPIDetailOwnership(profileBoardID, c)
		if err != nil || owned.Profile != oracleDetailProfile || owned.Domain != "*" {
			t.Fatal("Oracle detail ownership rejected", owned, err)
		}
		p, err := InspectAPIDetail(profileBoardID, c, source, Simple)
		if err != nil || p.Profile != oracleDetailProfile || p.Domain != "fixture.fa.em2.oraclecloud.com" || !strings.Contains(p.Endpoint, `ById;Id="123",siteNumber=CX_1`) || len(p.EnrichmentFields) == 0 {
			t.Fatal("Oracle detail tenant/mask binding lost", p, err)
		}
	}
	c["metadata"] = `{"scraper_type":"oracle_hcm","scraper_config":{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","enrich":["description"]}}`
	p, err := InspectAPIDetail(profileBoardID, c, "https://careers.example.net/jobs/123", Simple)
	if err != nil || p.Domain != "careers.example.net" || !strings.HasPrefix(p.Endpoint, "https://fixture.fa.em2.oraclecloud.com/") {
		t.Fatal("trusted vanity route rejected", err)
	}
	for _, metadata := range []string{`{"scraper_type":"oracle_hcm","scraper_config":{"host":"arbitrary.example.com","site":"CX_1"}}`, `{"scraper_type":"oracle_hcm","scraper_config":{"proxy":"enabled"}}`, `{"scraper_type":"oracle_hcm","scraper_config":{"enrich":["unknown"]}}`, `{"scraper_type":"oracle_hcm","scraper_config":{"fields":{"unported":"other"}}}`} {
		c["metadata"] = metadata
		if _, err := InspectAPIDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unported/unsafe Oracle option admitted", metadata)
		}
	}
}
