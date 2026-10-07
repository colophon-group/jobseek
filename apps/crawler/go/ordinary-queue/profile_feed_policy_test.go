package queue

import (
	"strings"
	"testing"
)

func TestRSSDOMFeedPolicyBindsBoundaryAndClassification(t *testing.T) {
	for _, kind := range []string{"generic", "successfactors", "teamtailor", "dom-rich", "dom-urls"} {
		t.Run(kind, func(t *testing.T) {
			config := profileConfig()
			config["crawler_type"] = "rss"
			config["board_url"] = "https://example.com/careers"
			md := `"preset":"` + kind + `","feed_url":"https://example.com/feed"`
			if kind == "successfactors" {
				md = `"preset":"successfactors","feed_url":"https://example.com/googlefeed.xml"`
			}
			if kind == "teamtailor" {
				md = `"preset":"teamtailor","feed_url":"https://example.com/jobs.rss"`
			}
			if strings.HasPrefix(kind, "dom-") {
				config["crawler_type"] = "dom"
				md = `"url_filter":"/jobs/"`
				if kind == "dom-rich" {
					md += `,"rich_rows":{"row_selector":"article","link_selector":"a"}`
				}
			}
			config["metadata"] = `{` + md + `,"scraper_type":"skip","url_allowlist":"https://example\\.com/jobs/[0-9]+","job_filter":{"field":"title","include":"Engineer","exclude":"Intern","require_classification":true}}`
			p, err := InspectRichMonitor(profileBoardID, config)
			if err != nil {
				t.Fatal("reviewed shared policy refused", err)
			}
			rules, err := FeedMonitorURLRules(config)
			if err != nil || !rules.RequiresRawInventory() {
				t.Fatal("policy not bound", err)
			}
			if match, err := rules.ProviderAllows("https://example.com/jobs/1"); err != nil || !match {
				t.Fatal("provider URL refused", err)
			}
			if match, err := rules.ProviderAllows("https://example.com/jobs/1/foreign"); err != nil || match {
				t.Fatal("allowlist used partial matching", err)
			}
			config["metadata"] = strings.Replace(config["metadata"], "Engineer", "Designer", 1)
			q, err := InspectRichMonitor(profileBoardID, config)
			if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
				t.Fatal("changed classifier retained write authority", err)
			}
		})
	}
}
