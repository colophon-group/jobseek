package queue

import "testing"

const sfRSSIdentityMetadata = `{"preset":"successfactors","resolve_job_invite_identity":true,"scraper_type":"skip","url_transform":{"find":"^https://example\\.com/job/[^/?#]+/([1-9]\\d{0,11})-[a-z]{2}_[A-Z]{2}/$","replace":"https://example.com/job-invite/\\1/","collision_policy":"prefer_source_pattern","collision_stream_buffer_limit":2000,"collision_identity_metadata_key":"job_invite_id","collision_canonical_identity_regex":"^https://example\\.com/job-invite/([1-9]\\d{0,11})/$","collision_preferred_source_patterns":["-de_DE/$","-en_US/$"]}}`

func TestRSSJobInviteIdentityBindsOnlySuccessFactorsFeed(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "rss"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = sfRSSIdentityMetadata
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || !p.RSSResolveJobInviteIdentity || p.RSSDetailEnrichment || !RSSDetailMonitorResourceMatches(p, "https://example.com/job/Alias/1/") {
		t.Fatal(p, err)
	}
	if RSSDetailMonitorResourceMatches(p, "https://foreign.example/job/Alias/1/") {
		t.Fatal("foreign identity resource admitted")
	}
	for _, md := range []string{
		`{"preset":"successfactors","resolve_job_invite_identity":true,"scraper_type":"skip"}`,
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
