package queue

import "testing"

func TestRealLastStaticDetailsColdRetirement(t *testing.T) {
	for _, provider := range []string{"onlyfy", "tupu360", "phuketall"} {
		t.Run(provider, func(t *testing.T) {
			testDetailFixtureRetirement(t, func(t *testing.T) firstOwnerFixture {
				source, domain, metadata := "", "", ""
				switch provider {
				case "onlyfy":
					source, domain, metadata = "https://fixture.onlyfy.jobs/de/job/abc", "fixture.onlyfy.jobs", `{"scraper_type":"onlyfy","scraper_config":{"language":"de","enrich":["description"]}}`
				case "tupu360":
					source, domain, metadata = "https://fixture.tupu360.com/fixture/position/detail?positionId=0123456789abcdef01234567", "fixture.tupu360.com", `{"scraper_type":"tupu360","scraper_config":{"listing_enrichment":true,"enrich":["description"]}}`
				case "phuketall":
					source, domain, metadata = "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html", "www.phuketall.com", `{"scraper_type":"phuketall"}`
				}
				return firstIndependentDetailFixtureAtBoard(t, metadata, source, domain, "https://"+domain+"/jobs")
			})
		})
	}
}
