package queue

import "testing"

func TestSitemapExplicitForeignRootBindsOnlyItsOriginAndConfiguredBudgets(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "sitemap", "sitemap", "sitemap"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"sitemap_url":"https://assets.example.com/jobs.xml","xml_attempts":5,"scraper_type":"json-ld"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	opts, _, _, err := SitemapMonitorConfig(c)
	if err != nil || opts.RootMaxAttempts != 5 || opts.ChildMaxAttempts != 5 || opts.RootContentAttempts != 5 {
		t.Fatal(opts, err)
	}
	if !initialMonitorResourceMatches(p, "https://assets.example.com/child.xml") || initialMonitorResourceMatches(p, "https://example.com/jobs.xml") || initialMonitorResourceMatches(p, "https://foreign.example/jobs.xml") {
		t.Fatal("explicit root confused with board origin")
	}
}

func TestRSSDetailFieldOptionsBindRequiredPropertiesAndResourceScope(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "rss", "rss", "rss"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"preset":"successfactors","feed_url":"https://example.com/googlefeed.xml","fetch_company":true,"detail_fields":{"company":"dept","adcode":"adcode"},"scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || !p.RSSDetailEnrichment {
		t.Fatal(p, err)
	}
	fields, required, err := RSSDetailFields(c)
	if err != nil || fields["company"] != "dept" || !required["company"] || !required["adcode"] {
		t.Fatal(fields, required, err)
	}
	for _, u := range []string{"https://example.com/job/1", "https://example.com:443/job/1?locale=en"} {
		if !initialMonitorResourceMatches(p, u) {
			t.Fatal("bound detail rejected", u)
		}
	}
	for _, u := range []string{"http://example.com/job/1", "https://foreign.example/job/1", "https://example.com:444/job/1", "https://user@example.com/job/1", "https://example.com/job/1#fragment"} {
		if initialMonitorResourceMatches(p, u) {
			t.Fatal("unbound detail accepted", u)
		}
	}
	for _, md := range []string{
		`{"preset":"generic","feed_url":"https://example.com/feed","fetch_company":true,"scraper_type":"skip"}`,
		`{"preset":"successfactors","fetch_company":1,"scraper_type":"skip"}`,
		`{"preset":"successfactors","detail_fields":{"Company":"dept"},"scraper_type":"skip"}`,
		`{"preset":"successfactors","detail_fields":{"company":"dept\""},"scraper_type":"skip"}`,
		`{"preset":"successfactors","detail_fields":{"company":"dept","company":"title"},"scraper_type":"skip"}`,
		`{"preset":"successfactors","detail_fields":[],"scraper_type":"skip"}`,
	} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("invalid required field contract admitted", md)
		}
	}
}
