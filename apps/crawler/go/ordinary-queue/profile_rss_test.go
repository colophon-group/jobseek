package queue

import "testing"

func TestTeamtailorRichProfileBindsFeedAndRefusesOtherRSSContracts(t *testing.T) {
	config := profileConfig()
	config["crawler_type"] = "rss"
	config["board_url"] = "https://careers.example.com/team"
	config["metadata"] = `{"preset":"teamtailor","scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.Endpoint != "https://careers.example.com/jobs.rss" || p.Profile != "rss.teamtailor-skip/v1" || p.Provider != "rss" {
		t.Fatalf("derived RSS feed/profile differs: %+v %v", p, err)
	}
	config["metadata"] = `{"preset":"teamtailor","feed_url":"https://feed.example.com/jobs.rss","scraper_type":"skip"}`
	explicit, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || explicit.Endpoint != "https://feed.example.com/jobs.rss" || explicit.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("explicit feed lost configuration binding", err)
	}
	for _, md := range []string{
		`{"preset":"successfactors","scraper_type":"skip"}`,
		`{"preset":"teamtailor","scraper_type":"json-ld"}`,
		`{"preset":"teamtailor","scraper_type":"skip","scraper_config":{"enrich":["title"]}}`,
		`{"preset":"teamtailor","scraper_type":"skip","pagination":{"page_size":1}}`,
		`{"preset":"teamtailor","scraper_type":"skip","feed_url":"http://example.com/jobs.rss"}`,
		`{"preset":"teamtailor","scraper_type":"skip","feed_url":"https://example.com/jobs.rss?offset=100"}`,
		`{"preset":"teamtailor","preset":"generic","scraper_type":"skip"}`,
	} {
		config["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unimplemented RSS profile admitted", md)
		}
	}
}
