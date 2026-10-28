package queue

import "testing"

func TestRenderedRSSWarmupIsImmutableAndNeverStatic(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"] = "rss", "1"
	c["board_url"] = "https://jobs.example/careers"
	c["metadata"] = `{"preset":"generic","feed_url":"https://jobs.example/feed","render":true,"warmup_url":"https://jobs.example/start","scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "rss.rendered-generic-skip/v1" {
		t.Fatal("rendered RSS warmup refused", err)
	}
	c["metadata"] = `{"preset":"generic","feed_url":"https://jobs.example/feed","render":true,"warmup_url":"https://jobs.example/other","scraper_type":"skip"}`
	other, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.EffectiveConfigSHA256 == other.EffectiveConfigSHA256 {
		t.Fatal("warmup was omitted from ownership binding", err)
	}
	for _, raw := range []string{
		`{"preset":"generic","feed_url":"https://jobs.example/feed","render":false,"warmup_url":"https://jobs.example/start","scraper_type":"skip"}`,
		`{"preset":"generic","feed_url":"https://jobs.example/feed","render":true,"warmup_url":"https://foreign.example/","scraper_type":"skip"}`,
		`{"preset":"generic","feed_url":"https://jobs.example/feed","render":true,"warmup_url":null,"scraper_type":"skip"}`,
	} {
		c["metadata"] = raw
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported warmup admitted")
		}
	}
}
