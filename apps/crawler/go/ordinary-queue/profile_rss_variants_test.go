package queue

import (
	"strings"
	"testing"
)

func TestGroupedRSSVariantProfilesBindParserAndTenant(t *testing.T) {
	for _, kind := range []string{"legacy_xml", "summary"} {
		for _, assignment := range []string{"skip", "json-ld"} {
			c := profileConfig()
			c["crawler_type"] = "rss"
			c["metadata"] = `{"preset":"successfactors","variant":"legacy_xml","company":"Fixture","feed_url":"https://career.example.com/career?company=Fixture&career_ns=job_listing_summary&resultType=XML","scraper_type":"` + assignment + `"}`
			name := "rss.successfactors-legacy-xml-"
			if kind == "summary" {
				c["metadata"] = `{"preset":"generic","description_mode":"title_employment_location","feed_url":"https://example.com/feed","scraper_type":"` + assignment + `"}`
				name = "rss.generic-summary-"
			}
			if assignment == "skip" {
				name += "skip/v1"
			} else {
				name += "items/v1"
			}
			p, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || p.Profile != name {
				t.Fatal("grouped RSS parser profile refused", kind, assignment, p.Profile, err)
			}
			changed := cloneConfig(c)
			if kind == "legacy_xml" {
				changed["metadata"] = strings.Replace(c["metadata"], `"company":"Fixture"`, `"company":"Other"`, 1)
			} else {
				changed["metadata"] = strings.Replace(c["metadata"], "title_employment_location", "unknown", 1)
			}
			if _, err := InspectRichMonitor(profileBoardID, changed); err == nil {
				t.Fatal("different tenant/parser admitted")
			}
		}
	}
}
