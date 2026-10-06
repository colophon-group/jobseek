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
		`{"preset":"successfactors","variant":"legacy","scraper_type":"skip"}`,
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

func TestSuccessFactorsRichProfileBindsOnlyFeedSkipContract(t *testing.T) {
	config := profileConfig()
	config["crawler_type"], config["board_url"] = "rss", "https://careers.example.com/team"
	config["metadata"] = `{"preset":"successfactors","scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.Endpoint != "https://careers.example.com/googlefeed.xml" || p.Profile != "rss.successfactors-skip/v1" {
		t.Fatal("derived SuccessFactors feed differs", p, err)
	}
	for _, feed := range []string{"https://feed.example.com/googlefeed.xml", "https://feed.example.com/GoogleFeed.xml/", "https://feed.example.com/services/rss/category/?catid=123"} {
		config["metadata"] = `{"preset":"successfactors","variant":"feed","feed_url":"` + feed + `","scraper_type":"skip"}`
		explicit, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || explicit.Endpoint != feed || explicit.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("explicit feed lost binding", explicit, err)
		}
	}
	for _, md := range []string{
		`{"preset":"successfactors","variant":"rmk","scraper_type":"skip"}`,
		`{"preset":"successfactors","variant":"legacy_xml","scraper_type":"skip"}`,
		`{"preset":"successfactors","scraper_type":"skip","job_filter":{"title":"Engineer"}}`,
		`{"preset":"successfactors","scraper_type":"skip","feed_url":"https://example.com/googlefeed.xml?page=2"}`,
		`{"preset":"successfactors","scraper_type":"skip","feed_url":"https://example.com/services/rss/category/?catid=0"}`,
		`{"preset":"successfactors","scraper_type":"skip","feed_url":"https://example.com/jobs.rss"}`,
	} {
		config["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unsupported SuccessFactors contract admitted", md)
		}
	}
}
