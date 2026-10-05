package queue

import "testing"

func TestJobylonSelectsGroupCompanyAndURLAndBindsOriginalMetadata(t *testing.T) {
	for _, row := range []struct {
		md, want string
		accepted bool
	}{
		{`{"company_id":"123","scraper_type":"json-ld"}`, "companies/123", true},
		{`{"company_id":123,"company_group_id":"456","scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`, "company-groups/456", true},
		{`{}`, "companies/123", true},
		{`{"company_id":0,"company_group_id":null}`, "companies/123", true},
		{`{"company_id":"../escape"}`, "", false},
		{`{"company_id":1.5}`, "", false},
		{`{"company_id":"123","company_id":"456"}`, "", false},
		{`{"company_id":"123","unreviewed":true}`, "", false},
		{`{"company_id":"123","scraper_config":{"enrich":["title"]}}`, "", false},
	} {
		c := profileConfig()
		c["crawler_type"], c["domain"], c["throttle_key"] = "jobylon", "jobylon", "jobylon"
		c["board_url"], c["metadata"] = "https://cdn.jobylon.com/jobs/companies/123/embed/v2/", row.md
		p, err := InspectRichMonitor(profileBoardID, c)
		if (err == nil) != row.accepted || err == nil && (p.Token != row.want || p.Profile != "jobylon.embed-items/v1" || p.Endpoint != "https://cdn.jobylon.com/jobs/"+row.want+"/embed/v2/") {
			t.Fatal("Jobylon endpoint selection/binding differs", row.md, p, err)
		}
	}
}

func TestJobylonBindsSemanticNestedConfigurationAndRefusesBrowserInventory(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "jobylon"
	c["metadata"] = `{"company_id":"123","scraper_type":"json-ld","scraper_config":{"enrich":["description"],"render":false}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	c["metadata"] = `{"scraper_config":{"render":false,"enrich":["description"]},"scraper_type":"json-ld","company_id":"123"}`
	other, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.EffectiveConfigSHA256 != other.EffectiveConfigSHA256 {
		t.Fatal("semantic binding differs", err)
	}
	c["monitor_needs_browser"] = "1"
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("browser inventory acquired direct owner")
	}
}

func TestJSONLDDescriptionMaskUsesExistingEnrichmentWriter(t *testing.T) {
	c := jsonldDetailConfig()
	c["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
	p, err := InspectJSONLDDetail(jsonldBoardID, c, "https://emp.jobylon.com/jobs/123/", Simple)
	if err != nil || len(p.EnrichmentFields) != 1 || p.EnrichmentFields[0] != "description" {
		t.Fatal("description mask unavailable", err)
	}
	for _, md := range []string{`{"scraper_type":"json-ld","scraper_config":{"enrich":["description","description"]}}`, `{"scraper_type":"json-ld","scraper_config":{"enrich":["description"],"fallback":["dom"]}}`} {
		c["metadata"] = md
		if _, err := InspectJSONLDDetail(jsonldBoardID, c, p.SourceURL, Simple); err == nil {
			t.Fatal("unproven detail contract acquired owner")
		}
	}
}
