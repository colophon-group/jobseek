package queue

import (
	"encoding/json"
	"testing"
)

func TestInferredRichNoScrapePreservesOriginalOverridesAndNoTransport(t *testing.T) {
	for _, provider := range []string{"api_sniffer", "nextdata"} {
		for _, browser := range []bool{false, true} {
			c := profileConfig()
			c["crawler_type"], c["domain"], c["throttle_key"] = provider, "example.com", "example.com"
			c["board_url"] = "https://example.com/careers"
			c["monitor_needs_browser"] = "0"
			md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "name"}}
			if provider == "nextdata" {
				md = map[string]any{"path": "jobs", "url_template": "https://example.com/jobs/{id}", "fields": map[string]any{"title": "title"}}
			}
			if browser {
				c["monitor_needs_browser"] = "1"
				if provider == "api_sniffer" {
					md["browser"] = true
				} else {
					md["render"] = true
				}
			}
			encode := func() { b, _ := json.Marshal(md); c["metadata"] = string(b) }
			encode()
			if _, e := InspectRichMonitor(profileBoardID, c); e != nil {
				t.Fatal(provider, browser, e)
			}
			assertSQLOnlySkipDetailOwnership(t, c)
			md["scraper_type"] = nil
			encode()
			assertSQLOnlySkipDetailOwnership(t, c)
			for _, override := range []any{"", "json-ld"} {
				md["scraper_type"] = override
				encode()
				if _, e := InspectNoScrapeDetail(profileBoardID, c, c["board_url"], Simple); e == nil {
					t.Fatal("explicit override lost")
				}
			}
			delete(md, "scraper_type")
			for _, enrich := range []any{nil, []any{}, []any{"description"}} {
				md["scraper_config"] = map[string]any{"enrich": enrich}
				encode()
				if _, e := InspectNoScrapeDetail(profileBoardID, c, c["board_url"], Simple); e == nil {
					t.Fatal("enrichment gained clear authority")
				}
			}
			delete(md, "scraper_config")
			md["fields"] = map[string]any{}
			encode()
			if _, e := InspectNoScrapeDetail(profileBoardID, c, c["board_url"], Simple); e == nil {
				t.Fatal("empty fields inferred rich")
			}
		}
	}
}
func TestRealInferredRichNoScrapeColdRetirement(t *testing.T) {
	for _, provider := range []string{"api_sniffer", "nextdata"} {
		t.Run(provider, func(t *testing.T) {
			md := `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"}}`
			if provider == "nextdata" {
				md = `{"render":true,"path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"}}`
			}
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				return firstIndependentDetailFixtureForProvider(t, provider, md, "https://jobs.example.net/job/no-scrape", "jobs.example.net", "https://example.com/careers")
			})
		})
	}
}
