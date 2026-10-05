package queue

import (
	"strings"
	"testing"
)

func TestEmbeddedDetailAdmissionBindsConfigAndActualRoute(t *testing.T) {
	c := apiDetailConfig("nextdata")
	c["crawler_type"] = "dom"
	c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"path":"props.pageProps.job","fields":{"title":"title","description":"description","locations":"locations[].name"},"enrich":["description"]}}`
	p, err := InspectEmbeddedDetail(profileBoardID, c, "https://careers.example.net/job/123", Simple)
	if err != nil || p.Profile != embeddedDetailProfile || !p.EmbeddedNextdata || p.Domain != "careers.example.net" || len(p.EnrichmentFields) != 1 {
		t.Fatal("configured nextdata detail refused", p, err)
	}
	owner, err := inspectDetailOwnership(profileBoardID, c)
	if err != nil || owner.Domain != "*" || owner.Profile != embeddedDetailProfile {
		t.Fatal("embedded independent admission differs", owner, err)
	}
	c["metadata"] = strings.Replace(c["metadata"], "description\"]", "employment_type\"]", 1)
	changed, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple)
	if err != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
		t.Fatal("enrichment change did not rebind ownership")
	}
	for _, source := range []string{"https://user:secret@careers.example.net/1", "https://careers.example.net:444/1", "file:///1"} {
		if _, err := InspectEmbeddedDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unsafe detail route admitted")
		}
	}
	for _, options := range []string{`{"proxy":true}`, `{"render":true}`, `{"fields":{"title":"["}}`, `{"fields":{"title":"title"},"enrich":["description","description"]}`, `{"fields":{"title":"title"},"proxy":true,"proxy":false}`} {
		c["metadata"] = `{"scraper_type":"nextdata","scraper_config":` + options + `}`
		if _, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple); err == nil {
			t.Fatal("unported or ambiguous configuration admitted")
		}
	}
}

func TestRenderedEmbeddedDetailUsesExistingNavigationAndBrowserQueue(t *testing.T) {
	c := apiDetailConfig("nextdata")
	c["scraper_needs_browser"] = "1"
	c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"render":true,"wait":"domcontentloaded","path":"job","fields":{"title":"title","description":"description"}}}`
	p, err := InspectRenderedDetail(profileBoardID, c, "https://careers.example.net/job/123", Browser)
	if err != nil || p.Profile != embeddedRenderedDetailProfile || !p.EmbeddedNextdata || detailWorker(p.Profile) != Browser {
		t.Fatal("rendered Next.js profile refused", p, err)
	}
	if _, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple); err == nil {
		t.Fatal("rendered detail downgraded to direct HTTP")
	}
}
func TestRealRenderedEmbeddedRetirementUsesExistingColdReversal(t *testing.T) {
	testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
		return firstIndependentDetailFixture(t, `{"scraper_type":"nextdata","scraper_config":{"render":true,"path":"job","fields":{"title":"title","description":"description"}}}`, "https://careers.example.net/job/123", "careers.example.net", Browser)
	})
}
