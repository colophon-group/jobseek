package queue

import "testing"

func TestGroupedRSSProviderProfilesBindCanonicalFeedAndDetails(t *testing.T) {
	for _, c := range []struct{ preset, board, metadata, feed string }{
		{"governmentjobs", "https://www.governmentjobs.com/careers/Fixture/", `"agency":"fixture"`, "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=fixture"},
		{"zoho_recruit", "https://fixture.zohorecruit.eu/jobs/Careers", `"tenant":"fixture.eu","feed_url":"https://fixture.zohorecruit.eu/jobs/Careers/rss"`, "https://fixture.zohorecruit.eu/jobs/Careers/rss"},
	} {
		t.Run(c.preset, func(t *testing.T) {
			cfg := profileConfig()
			cfg["crawler_type"], cfg["board_url"] = "rss", c.board
			cfg["metadata"] = `{"preset":"` + c.preset + `",` + c.metadata + `,"scraper_type":"skip"}`
			before := cfg["metadata"]
			p, err := InspectRichMonitor(profileBoardID, cfg)
			if err != nil || p.Profile != "rss."+c.preset+"-skip/v1" || p.Endpoint != c.feed || cfg["metadata"] != before || !initialMonitorResourceMatches(p, c.feed) || initialMonitorResourceMatches(p, c.feed+"&other=1") {
				t.Fatal("provider feed binding differs", p, err)
			}
			cfg["metadata"] = `{"preset":"` + c.preset + `",` + c.metadata + `,"scraper_type":"json-ld"}`
			detail, err := InspectRichMonitor(profileBoardID, cfg)
			if err != nil || detail.Profile != "rss."+c.preset+"-items/v1" || detail.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("detail assignment/hash binding differs", detail, err)
			}
			cfg["monitor_needs_browser"] = "1"
			if _, err := InspectRichMonitor(profileBoardID, cfg); err == nil {
				t.Fatal("unimplemented rendered provider admitted")
			}
		})
	}
}

func TestGovernmentJobsRefusesTenantAndFeedDrift(t *testing.T) {
	for _, c := range []struct{ board, extra string }{
		{"https://www.governmentjobs.com/careers/fixture", `"agency":"other"`},
		{"https://www.governmentjobs.com/careers/fixture", `"feed_url":"https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=other"`},
		{"https://www.governmentjobs.com/careers/fixture", `"feed_url":""`},
		{"https://www.governmentjobs.com/careers/fixture?filter=one", `"agency":"fixture"`},
		{"https://foreign.example.com/careers/fixture", `"agency":"fixture"`},
		{"https://www.governmentjobs.com/careers/fixture/jobs/1", `"agency":"fixture"`},
		{"https://www.governmentjobs.com/careers/fixture", `"agency":true`},
		{"https://www.governmentjobs.com/careers/fixture", `"preset":"generic"`},
	} {
		cfg := profileConfig()
		cfg["crawler_type"], cfg["board_url"] = "rss", c.board
		cfg["metadata"] = `{"preset":"governmentjobs","scraper_type":"skip",` + c.extra + `}`
		if _, err := InspectRichMonitor(profileBoardID, cfg); err == nil {
			t.Fatal("tenant/endpoint drift admitted", c)
		}
	}
}

func TestZohoRSSSourceIdentityRequiresURLTenantAndNumericID(t *testing.T) {
	for _, c := range []struct {
		source, identity string
		valid            bool
	}{
		{"https://fixture.zohorecruit.eu/jobs/123", "zoho_recruit:fixture.eu:123", true},
		{"https://fixture.zohorecruit.com.au/jobs/123", "zoho_recruit:fixture.com.au:123", true},
		{"https://other.example/jobs/123", "", true},
		{"https://fixture.zohorecruit.eu/jobs/123", "zoho_recruit:other.eu:123", false},
		{"https://fixture.zohorecruit.eu/jobs/123", "zoho_recruit:fixture.eu:１２３", false},
		{"https://fixture.zohorecruit.eu/jobs/123", "zoho_recruit:fixture.eu:123:other", false},
		{"https://other.example/jobs/123", "zoho_recruit:fixture.eu:123", false},
	} {
		if got := validZohoRSSIdentity(c.source, c.identity); got != c.valid {
			t.Fatal("source identity validation differs", c, got)
		}
	}
}
