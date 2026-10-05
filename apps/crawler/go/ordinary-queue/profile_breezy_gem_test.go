package queue

import (
	"strings"
	"testing"
)

func TestBreezyGemSelectExactProviderEndpointAndBindConfiguration(t *testing.T) {
	for _, provider := range []string{"breezy", "gem"} {
		t.Run(provider, func(t *testing.T) {
			c := profileConfig()
			c["crawler_type"], c["throttle_key"], c["domain"] = provider, provider, provider
			c["board_url"] = "https://jobs.gem.com/fixture"
			c["metadata"] = `{"scraper_type":"skip","slug":"ignored"}`
			want := "https://api.gem.com/job_board/v0/fixture/job_posts/"
			if provider == "breezy" {
				c["board_url"] = "https://fixture.breezy.hr/p/job"
				c["metadata"] = `{"scraper_type":"json-ld","slug":"ignored"}`
				want = "https://fixture.breezy.hr/json"
			}
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil || p.Endpoint != want {
				t.Fatal(p, e)
			}
			c["metadata"] = strings.TrimSuffix(c["metadata"], "}") + `,"recent_discovered_counts":[2],"suspect_streak":1}`
			q, e := InspectRichMonitor(profileBoardID, c)
			if e != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
				t.Fatal("runtime inventory metadata changed authority", e)
			}
			if provider == "gem" {
				c["metadata"] = `{"token":"explicit","scraper_type":"skip"}`
				want = "https://api.gem.com/job_board/v0/explicit/job_posts/"
			} else {
				c["metadata"] = `{"portal_url":"https://careers.example.com/path","scraper_type":"dom","scraper_config":{"steps":[]}}`
				want = "https://careers.example.com/json"
			}
			q, e = InspectRichMonitor(profileBoardID, c)
			if e != nil || q.Endpoint != want || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("explicit request not bound", q, e)
			}
			if provider == "gem" {
				c["metadata"] = `{"token":"../../escape"}`
				if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
					t.Fatal("unsafe Gem endpoint admitted")
				}
			}
			for _, md := range []string{`{"unknown":true}`, `{"token":"first","token":"second"}`, `{"portal_url":"https://user:pass@example.com"}`} {
				c["metadata"] = md
				if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
					t.Fatal("unsafe/unknown input admitted", md)
				}
			}
		})
	}
}
