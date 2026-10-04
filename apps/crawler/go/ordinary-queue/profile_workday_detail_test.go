package queue

import (
	"testing"
)

func workdayDetailConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"] = "workday"
	c["board_url"] = "https://fixture.wd1.myworkdayjobs.com/en-US/Careers"
	c["metadata"] = `{"scraper_type":"workday"}`
	return c
}

func TestWorkdayDetailAdmissionBindsSourceTenantAndIndependentDomain(t *testing.T) {
	c := workdayDetailConfig()
	c["domain"], c["throttle_key"] = "workday", "workday"
	const source = "https://fixture.wd1.myworkdayjobs.com/en-US/OtherSite/job/Senior%20Engineer/JR001"
	p, err := InspectWorkdayDetail(profileBoardID, c, source, Simple)
	if err != nil || p.Domain != "fixture.wd1.myworkdayjobs.com" || p.Endpoint != "https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/OtherSite/job/Senior%20Engineer/JR001" || p.Profile != "workday.cxs-detail/v1" {
		t.Fatalf("detail source admission changed: %+v / %v", p, err)
	}
	c["metadata"] = `{"scraper_config":{"facility_tenant_aliases":[" Alias "]}}`
	inferred, err := InspectWorkdayDetail(profileBoardID, c, source, Simple)
	if err != nil || len(inferred.FacilityTenantAliases) != 1 || inferred.FacilityTenantAliases[0] != "Alias" || p.EffectiveBoardSHA256 == inferred.EffectiveBoardSHA256 {
		t.Fatal("inferred scraper or immutable facility settings lost", err)
	}
	for _, bad := range []string{
		"https://other.wd1.myworkdayjobs.com/Careers/job/JR001",
		"https://fixture.wd2.myworkdayjobs.com/Careers/job/JR001",
		"https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001?next=1",
		"https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001%2fadmin",
	} {
		if _, err := InspectWorkdayDetail(profileBoardID, c, bad, Simple); err == nil {
			t.Errorf("foreign/ambiguous source admitted: %q", bad)
		}
	}
}

func TestWorkdayDetailAdmissionRetainsOtherScrapersAndTransportRoutes(t *testing.T) {
	const source = "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001"
	for _, metadata := range []string{
		`{"scraper_type":"json-ld"}`, `{"scraper_type":"onlyfy"}`,
		`{"scraper_config":{"proxy":true}}`, `{"scraper_config":{"render":true}}`,
		`{"ssl_verify":false}`, `{"scraper_config":{"skip_ssl":true}}`,
		`{"scraper_config":{"proxy":true,"proxy":false}}`,
		`{"scraper_config":{"facility_tenant_aliases":[""]}}`,
	} {
		c := workdayDetailConfig()
		c["metadata"] = metadata
		if _, err := InspectWorkdayDetail(profileBoardID, c, source, Simple); err == nil {
			t.Errorf("unsupported detail route admitted: %s", metadata)
		}
	}
	c := workdayDetailConfig()
	if _, err := InspectWorkdayDetail(profileBoardID, c, source, Browser); err == nil {
		t.Fatal("browser detail task admitted to direct HTTP")
	}
	c["scraper_needs_browser"] = "1"
	if _, err := InspectWorkdayDetail(profileBoardID, c, source, Simple); err == nil {
		t.Fatal("canonical browser detail admitted to simple queue")
	}
}
