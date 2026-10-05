package queue

import "testing"

func TestPhenomBindsDirectOriginAndRefusesUnprovenProxyTransport(t *testing.T) {
	for _, row := range []struct {
		md       string
		accepted bool
	}{
		{`{"sitemap_url":"https://example.com/sitemap.xml","keep_languages":["en","es-mx"],"url_exclude":"test","scraper_type":"json-ld"}`, true},
		{`{"sitemap_url":"https://example.com/sitemap.xml","source":"phenom_canvas","path":"unused","pagination":{"mode":"offset"},"slug_fields":["title"],"url_template":"unused","scraper_config":{"render":true,"proxy":true},"rescrape_policy":"never"}`, true},
		{`{"sitemap_url":"https://foreign.example/sitemap.xml"}`, false},
		{`{"proxy":true}`, false},
		{`{"keep_languages":["en",1]}`, false},
		{`{"keep_languages":["en"],"keep_languages":["de"]}`, false},
		{`{"url_exclude":"["}`, false},
	} {
		c := profileConfig()
		c["crawler_type"], c["metadata"], c["board_url"] = "phenom", row.md, "https://example.com/careers"
		p, err := InspectRichMonitor(profileBoardID, c)
		if (err == nil) != row.accepted || err == nil && p.Profile != "phenom.sitemap-urls/v1" {
			t.Fatal(p, err, row)
		}
		if err == nil && (!initialMonitorResourceMatches(p, "https://example.com/child.xml") || initialMonitorResourceMatches(p, "https://foreign.example/child.xml")) {
			t.Fatal("Phenom child origin escaped")
		}
	}
}
