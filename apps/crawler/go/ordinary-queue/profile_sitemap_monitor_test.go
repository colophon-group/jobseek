package queue

import "testing"

func TestSitemapMonitorBindsExplicitResourceFiltersAndDetailConfig(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "sitemap", "sitemap", "sitemap"
	c["board_url"] = "https://example.com/careers"
	c["metadata"] = `{"sitemap_url":"https://example.com/jobs.xml","url_filter":{"include":"/jobs/\\w+","exclude":"intern"},"scraper_type":"json-ld"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "sitemap.explicit-urls/v1" || p.Endpoint != "https://example.com/jobs.xml" {
		t.Fatal(p, err)
	}
	c["metadata"] = `{"sitemap_url":"https://example.com/jobs.xml","url_filter":"/jobs/","scraper_type":"dom","scraper_config":{"steps":[]}}`
	c["scraper_needs_browser"] = "1"
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("separate detail/filter configuration lost", err)
	}
	for _, md := range []string{
		`{"sitemap_url":"http://example.com/jobs.xml"}`,
		`{"sitemap_url":"https://foreign.com/jobs.xml"}`,
		`{"sitemap_url":"https://example.com:443/jobs.xml"}`,
		`{"sitemap_url":"https://example.com/jobs.xml#fragment"}`,
		`{"sitemap_url":"https://example.com/jobs.xml","xml_attempts":true}`,
		`{"sitemap_url":"https://example.com/jobs.xml","xml_attempts":2}`,
		`{"sitemap_url":"https://example.com/jobs.xml","proxy":true}`,
		`{"sitemap_url":"https://example.com/jobs.xml","ssl_verify":false}`,
		`{"sitemap_url":"https://example.com/jobs.xml","url_filter":{"include":"["}}`,
		`{"sitemap_url":"https://example.com/jobs.xml","url_filter":{"include":1}}`,
		`{"sitemap_url":"https://example.com/jobs.xml","url_rewrite":{}}`,
	} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported sitemap admitted", md)
		}
	}
}
