package queue

import "testing"

func TestBrowserAPIDetailBindsOriginalConfigurationAndWorker(t *testing.T) {
	c := apiDetailConfig("api_sniffer")
	c["scraper_needs_browser"] = "1"
	c["metadata"] = `{"scraper_type":"api_sniffer","scraper_config":{"fields":{"title":"title"},"settle":0.25}}`
	p, err := InspectBrowserAPIDetail(profileBoardID, c, "https://jobs.example.net/42", Browser)
	if err != nil || p.Profile != browserAPIDetailProfile || detailWorker(p.Profile) != Browser || p.Endpoint != p.SourceURL || p.Domain != "jobs.example.net" {
		t.Fatal("browser API admission failed", err)
	}
	owner, err := inspectDetailOwnership(profileBoardID, c)
	if err != nil || owner.Domain != "*" || owner.Endpoint != "" || owner.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 {
		t.Fatal("ownership invented source or changed fingerprint", err)
	}
	if _, err := InspectBrowserAPIDetail(profileBoardID, c, p.SourceURL, Simple); err == nil {
		t.Fatal("browser detail migrated to wrong queue")
	}
	for _, raw := range []string{`{"proxy":true}`, `{"persistent_context":true}`, `{"fields":{"title":"title"},"api_url":"https://example.com/api"}`, `{"fields":{"title":"title","title":"name"}}`} {
		c["metadata"] = `{"scraper_type":"api_sniffer","scraper_config":` + raw + `}`
		if _, err := InspectBrowserAPIDetail(profileBoardID, c, p.SourceURL, Browser); err == nil {
			t.Fatal("unsupported or ambiguous controls admitted", raw)
		}
	}
}

func TestRealBrowserAPIDetailOwnershipAndColdRetirement(t *testing.T) {
	metadata := `{"scraper_type":"api_sniffer","scraper_config":{"fields":{"title":"title","description":"description"}}}`
	fixture := func(t *testing.T) firstOwnerFixture {
		return firstIndependentDetailFixture(t, metadata, "https://jobs.example.net/job/native-api-detail", "jobs.example.net", Browser)
	}
	t.Run("ownership", func(t *testing.T) {
		testIndependentDetailOwnsPosting(t, fixture(t), browserAPIDetailProfile, "jobs.example.net")
	})
	t.Run("cold-retirement", func(t *testing.T) { testIndependentDetailColdRetirement(t, fixture) })
}
