package queue

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func assertSQLOnlySkipDetailOwnership(t *testing.T, config map[string]string) {
	t.Helper()
	worker, expected := Simple, noScrapeDetailProfile
	if config["scraper_needs_browser"] == "1" {
		worker, expected = Browser, browserNoScrapeDetailProfile
	}
	p, err := inspectDetailOwnership(profileBoardID, config)
	if err != nil || p.Profile != expected || !NoScrapeDetailProfile(p.Profile) || detailWorker(p.Profile) != worker || p.Domain != "*" || ProfileRequiresProxy(p.Profile) {
		t.Fatal("explicit skip lost SQL-only ownership or gained transport", p, err)
	}
	if _, err := InspectJSONLDDetail(profileBoardID, config, config["board_url"], worker); err == nil {
		t.Fatal("explicit skip gained JSON-LD fetch authority")
	}
	if _, err := InspectDOMDetail(profileBoardID, config, config["board_url"], worker); err == nil {
		t.Fatal("explicit skip gained DOM fetch authority")
	}
	if _, err := InspectRenderedDetail(profileBoardID, config, config["board_url"], worker); err == nil {
		t.Fatal("explicit skip gained rendered fetch authority")
	}
}

func TestExplicitNoScrapeProfileMatchesOriginalClassifierAndSQL(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_explicit_no_scrape.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixture struct {
		Source string `json:"source_revision"`
		Cases  []struct {
			CrawlerType string `json:"crawler_type"`
			Metadata    json.RawMessage
			Eligible    bool `json:"explicit_clear_eligible"`
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || fixture.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || len(fixture.Cases) != 147 {
		t.Fatal("original SQL/classifier reference missing")
	}
	positive := 0
	for _, c := range fixture.Cases {
		if c.Eligible {
			positive++
		}
		for _, worker := range []WorkerType{Simple, Browser} {
			config := profileConfig()
			config["crawler_type"], config["metadata"] = c.CrawlerType, string(c.Metadata)
			config["scraper_needs_browser"] = "0"
			if worker == Browser {
				config["scraper_needs_browser"] = "1"
			}
			p, e := InspectNoScrapeDetail(profileBoardID, config, "https://jobs.example.net/job/42", worker)
			if (e == nil) != c.Eligible {
				t.Fatalf("original explicit skip classification differs for %s metadata%s: %v", c.CrawlerType, c.Metadata, e)
			}
			if e == nil && (!NoScrapeDetailProfile(p.Profile) || detailWorker(p.Profile) != worker || p.Domain != "jobs.example.net" || ProfileRequiresProxy(p.Profile)) {
				t.Fatal("SQL-only route gained transport or lost worker binding", p)
			}
		}
	}
	if positive != 28 {
		t.Fatal("original positive cases changed", positive)
	}
}

func TestNoScrapeProfilePreservesImmutableScopeAndRejectsUnprovedMetadata(t *testing.T) {
	c := profileConfig()
	c["metadata"] = `{"scraper_type":"skip","scraper_config":{"proxy":true}}`
	p, e := InspectNoScrapeDetail(profileBoardID, c, "https://jobs.example.net/job/42", Simple)
	if e != nil {
		t.Fatal(e)
	}
	changed := cloneConfig(c)
	changed["metadata"] = `{"scraper_config":{"proxy":false},"scraper_type":"skip"}`
	other, e := InspectNoScrapeDetail(profileBoardID, changed, p.SourceURL, Simple)
	if e != nil || other.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
		t.Fatal("ignored fetch options lost original binding", e)
	}
	for _, md := range []string{`{"scraper_type":"skip","scraper_type":"json-ld"}`, `{"scraper_type":"skip","scraper_config":"unproved"}`, `{"scraper_type":"skip","scraper_config":{"enrich":[]}}`, `{"scraper_type":"skip","scraper_config":{"enrich":null}}`, `{"scraper_type":"skip","scraper_config":{"enrich":["description"]}}`, `{"scraper_type":"skip","scraper_config":{"enrich":null,"enrich":[]}}`} {
		changed["metadata"] = md
		if _, e := InspectNoScrapeDetail(profileBoardID, changed, p.SourceURL, Simple); e == nil {
			t.Fatal("unproved clear route admitted", md)
		}
	}
	for _, source := range []string{"https://user:secret@example.com/job/42", "file:///tmp/job", "https://example.com/" + strings.Repeat("x", 8192)} {
		if _, e := InspectNoScrapeDetail(profileBoardID, c, source, Simple); e == nil {
			t.Fatal("unproved source accepted")
		}
	}
	if _, e := InspectNoScrapeDetail(profileBoardID, c, p.SourceURL, Browser); e == nil {
		t.Fatal("wrong worker acquired clear authority")
	}
}

func TestRealExplicitNoScrapeColdRetirementConservesBothWorkerNamespaces(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		t.Run(string(worker), func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				return firstIndependentDetailFixture(t, `{"scraper_type":"skip","scraper_config":{"proxy":true}}`, "https://jobs.example.net/job/native-no-scrape", "jobs.example.net", worker)
			})
		})
	}
}
