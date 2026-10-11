package queue

import "testing"

func TestRSSJobInviteIdentityBindsOnlySuccessFactorsFeed(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "rss"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"preset":"successfactors","resolve_job_invite_identity":true,"scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || !p.RSSResolveJobInviteIdentity || p.RSSDetailEnrichment || !RSSDetailMonitorResourceMatches(p, "https://example.com/job/Alias/1/") {
		t.Fatal(p, err)
	}
	if RSSDetailMonitorResourceMatches(p, "https://foreign.example/job/Alias/1/") {
		t.Fatal("foreign identity resource admitted")
	}
	for _, md := range []string{
		`{"preset":"teamtailor","resolve_job_invite_identity":true,"scraper_type":"skip"}`,
		`{"preset":"successfactors","resolve_job_invite_identity":null,"scraper_type":"skip"}`,
		`{"preset":"successfactors","resolve_job_invite_identity":1,"scraper_type":"skip"}`,
		`{"preset":"successfactors","variant":"rmk","resolve_job_invite_identity":true,"scraper_type":"skip"}`,
	} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported identity controller admitted")
		}
	}
}
