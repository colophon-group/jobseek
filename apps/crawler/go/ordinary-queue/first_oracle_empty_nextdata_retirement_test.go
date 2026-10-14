package queue

import "testing"

func TestRealOracleVanityAndEmptyNextdataColdRetirement(t *testing.T) {
	for _, kind := range []string{"oracle-vanity", "nextdata-empty"} {
		t.Run(kind, func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				if kind == "oracle-vanity" {
					return firstIndependentDetailFixtureAtBoard(t, `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`, "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123", "fixture.fa.em2.oraclecloud.com", "https://careers.example.net/")
				}
				return firstIndependentDetailFixtureAtBoard(t, `{"scraper_type":"nextdata"}`, "https://join.com/companies/native/123-engineer", "join.com", "https://join.com/companies/native")
			})
		})
	}
}
