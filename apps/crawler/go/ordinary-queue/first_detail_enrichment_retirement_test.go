package queue

import (
	"fmt"
	"testing"
)

func TestRealSharedDetailEnrichmentColdRetirementConservesAttempts(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		for _, field := range []string{"description", "locations"} {
			t.Run(scraper+"/"+field, func(t *testing.T) {
				testDetailFixtureRetirement(t, func(t *testing.T) firstOwnerFixture {
					options := fmt.Sprintf(`"enrich":[%q]`, field)
					if scraper == "dom" {
						options += `,"steps":[{"tag":"h1","field":"title"},{"tag":"article","field":"description"}]`
					}
					metadata := fmt.Sprintf(`{"scraper_type":%q,"scraper_config":{%s}}`, scraper, options)
					return firstIndependentDetailFixture(t, metadata, "https://careers.example.net/job/123", "careers.example.net")
				})
			})
		}
	}
}

func TestRealDOMDocumentFormatsColdRetirementConservesAttempts(t *testing.T) {
	for _, format := range []string{"pdf", "docx", "doc"} {
		t.Run(format, func(t *testing.T) {
			testDetailFixtureRetirement(t, func(t *testing.T) firstOwnerFixture {
				metadata := fmt.Sprintf(`{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"document_fallback":{%q:{"title_source":"text"}}}}`, format)
				return firstIndependentDetailFixture(t, metadata, "https://careers.example.net/download/123", "careers.example.net")
			})
		})
	}
}
