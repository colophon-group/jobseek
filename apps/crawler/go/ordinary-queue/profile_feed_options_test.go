package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestFeedDetailAssignmentsMatchActualPythonProcessor(t *testing.T) {
	var corpus struct {
		Assignments []struct {
			Provider string
			Metadata json.RawMessage
			Enrich   []string
		}
	}
	body, err := os.ReadFile("../ordinary-worker/testdata/python_feed_urls.json")
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Assignments) != 32 {
		t.Fatal("actual processor assignment corpus unavailable", err)
	}
	for _, c := range corpus.Assignments {
		config := profileConfig()
		config["crawler_type"] = c.Provider
		config["metadata"] = string(c.Metadata)
		if err := feedRichDetailAssignment(config); err != nil || len(c.Enrich) != 0 {
			t.Fatal("configured scraper became implicit detail enrichment", string(c.Metadata), err)
		}
	}
}

func TestFeedAssignmentBindingsRetainNestedConfigAndBrowserDetails(t *testing.T) {
	for _, provider := range []string{"personio", "rss", "sitemap"} {
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["scraper_needs_browser"] = provider, "https://example.com/careers", "1"
		prefix := `"slug":"fixture",`
		if provider == "rss" {
			prefix = `"preset":"teamtailor","feed_url":"https://example.com/jobs.rss",`
		}
		if provider == "sitemap" {
			prefix = `"sitemap_url":"https://example.com/jobs.xml","url":"https://unused.example.com/",`
		}
		c["metadata"] = `{` + prefix + `"scraper_type":"dom","scraper_config":{"steps":[{"tag":"p","field":"description"}]}}`
		first, err := InspectRichMonitor(profileBoardID, c)
		if err != nil {
			t.Fatal(provider, err)
		}
		c["metadata"] = `{"scraper_config":{"steps":[{"field":"description","tag":"p"}]},` + prefix + `"scraper_type":"dom"}`
		second, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || first.EffectiveConfigSHA256 != second.EffectiveConfigSHA256 {
			t.Fatal("nested semantic config binding lost", provider, err)
		}
		c["metadata"] = `{` + prefix + `"scraper_type":"dom","scraper_config":{"steps":[{"tag":"div","field":"description"}]}}`
		changed, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || first.EffectiveConfigSHA256 == changed.EffectiveConfigSHA256 {
			t.Fatal("changed detail assignment lost binding", provider, err)
		}
	}
}

func TestFeedURLRulesRefuseUnimplementedIdentityOrDelegation(t *testing.T) {
	for _, metadata := range []string{
		`{"url_allowlist":""}`,
		`{"url_allowlist":"["}`,
		`{"url_allowlist":null}`,
		`{"url_transform":{"find":"x","replace":"{identity}"}}`,
		`{"url_transform":{"find":"x","replace":"y","collision_policy":"prefer_source_pattern"}}`,
		`{"url_transform":{"find":"(","replace":"y"}}`,
		`{"url_transform":{"find":"x","replace":"\\1"}}`,
		`{"url_filter":{"include":"["}}`,
	} {
		if _, err := FeedMonitorURLRules(map[string]string{"metadata": metadata}); err == nil {
			t.Fatal("unimplemented policy admitted", metadata)
		}
	}
	for _, metadata := range []string{`{"scraper_type":"dom","scraper_config":{"enrich":["description"]}}`, `{"scraper_type":"skip","scraper_config":{"enrich":["title"]}}`} {
		if feedRichDetailAssignment(map[string]string{"metadata": metadata}) == nil {
			t.Fatal("unqualified delegation admitted", metadata)
		}
	}
}
