package queue

import "testing"

func TestSitemapResponseResourceBindsEveryChildToOriginalOrigin(t *testing.T) {
	p := GreenhouseMonitorProfile{Provider: "sitemap", Endpoint: "https://example.com/jobs.xml"}
	for _, resource := range []string{"https://example.com/jobs.xml", "https://example.com/nested/jobs.xml?shard=2"} {
		if !SitemapMonitorResourceMatches(p, resource) || !initialMonitorResourceMatches(p, resource) {
			t.Fatal("same-origin sitemap child rejected", resource)
		}
	}
	for _, resource := range []string{"http://example.com/jobs.xml", "https://foreign.example/jobs.xml", "https://example.com:443/jobs.xml", "https://user@example.com/jobs.xml", "https://example.com/jobs.xml#fragment", "/jobs.xml", "https://example.com/jobs%zz.xml"} {
		if SitemapMonitorResourceMatches(p, resource) || initialMonitorResourceMatches(p, resource) {
			t.Fatal("unbound sitemap child accepted", resource)
		}
	}
	p.Provider = "workday"
	if SitemapMonitorResourceMatches(p, p.Endpoint) {
		t.Fatal("foreign provider accepted")
	}
}

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
		`{"sitemap_url":"https://example.com/jobs.xml","proxy":"enabled"}`,
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
