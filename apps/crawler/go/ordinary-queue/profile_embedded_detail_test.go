package queue

import (
	"encoding/csv"
	"encoding/json"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"io"
	"os"
	"strings"
	"testing"
)

func TestEmbeddedDetailAdmissionBindsConfigAndActualRoute(t *testing.T) {
	c := apiDetailConfig("nextdata")
	c["crawler_type"] = "dom"
	c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"path":"props.pageProps.job","fields":{"title":"title","description":"description","locations":"locations[].name"},"enrich":["description"]}}`
	p, err := InspectEmbeddedDetail(profileBoardID, c, "https://careers.example.net/job/123", Simple)
	if err != nil || p.Profile != embeddedDetailProfile || !p.EmbeddedNextdata || p.Domain != "careers.example.net" || len(p.EnrichmentFields) != 1 {
		t.Fatal("configured nextdata detail refused", p, err)
	}
	owner, err := inspectDetailOwnership(profileBoardID, c)
	if err != nil || owner.Domain != "*" || owner.Profile != embeddedDetailProfile {
		t.Fatal("embedded independent admission differs", owner, err)
	}
	c["metadata"] = strings.Replace(c["metadata"], "description\"]", "employment_type\"]", 1)
	changed, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple)
	if err != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
		t.Fatal("enrichment change did not rebind ownership")
	}
	for _, source := range []string{"https://user:secret@careers.example.net/1", "https://careers.example.net:444/1", "file:///1"} {
		if _, err := InspectEmbeddedDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unsafe detail route admitted")
		}
	}
	for _, options := range []string{`{"proxy":true}`, `{"render":true}`, `{"fields":{"title":"["}}`, `{"fields":{"title":"title"},"enrich":["description","description"]}`, `{"fields":{"title":"title"},"proxy":true,"proxy":false}`} {
		c["metadata"] = `{"scraper_type":"nextdata","scraper_config":` + options + `}`
		if _, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple); err == nil {
			t.Fatal("unported or ambiguous configuration admitted")
		}
	}
}

func TestRenderedEmbeddedDetailUsesExistingNavigationAndBrowserQueue(t *testing.T) {
	c := apiDetailConfig("nextdata")
	c["scraper_needs_browser"] = "1"
	c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"render":true,"wait":"domcontentloaded","path":"job","fields":{"title":"title","description":"description"}}}`
	p, err := InspectRenderedDetail(profileBoardID, c, "https://careers.example.net/job/123", Browser)
	if err != nil || p.Profile != embeddedRenderedDetailProfile || !p.EmbeddedNextdata || detailWorker(p.Profile) != Browser {
		t.Fatal("rendered Next.js profile refused", p, err)
	}
	if _, err := InspectEmbeddedDetail(profileBoardID, c, p.SourceURL, Simple); err == nil {
		t.Fatal("rendered detail downgraded to direct HTTP")
	}
}
func TestRealRenderedEmbeddedRetirementUsesExistingColdReversal(t *testing.T) {
	testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
		return firstIndependentDetailFixture(t, `{"scraper_type":"nextdata","scraper_config":{"render":true,"path":"job","fields":{"title":"title","description":"description"}}}`, "https://careers.example.net/job/123", "careers.example.net", Browser)
	})
}

// Exercise the actual registry assignment so a unit-only synthetic configuration
// cannot hide a dropped selected field or a locale-dependent browser action.
func TestEricssonRegistryRetainsChineseLocaleAndSelectedDetailMask(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	reader := csv.NewReader(f)
	header, err := reader.Read()
	if err != nil {
		t.Fatal(err)
	}
	indexes := map[string]int{}
	for n, k := range header {
		indexes[k] = n
	}
	found := false
	for {
		row, e := reader.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			t.Fatal(e)
		}
		if row[indexes["board_slug"]] != "ericsson-china-all" {
			continue
		}
		found = true
		var options map[string]any
		if json.Unmarshal([]byte(row[indexes["scraper_config"]]), &options) != nil {
			t.Fatal("registry scraper options invalid")
		}
		pipeline, e := actions.Parse(options["actions"])
		if e != nil || len(pipeline) != 3 || pipeline[0].Kind != "click" || pipeline[0].Required || pipeline[1].Kind != "wait_for" || !pipeline[1].Required || pipeline[2].Kind != "evaluate" || !pipeline[2].Required {
			t.Fatal("locale readiness/extraction action contract changed")
		}
		c := apiDetailConfig("nextdata")
		c["scraper_needs_browser"] = "1"
		raw, _ := json.Marshal(map[string]any{"scraper_type": "nextdata", "scraper_config": options})
		c["metadata"] = string(raw)
		p, e := InspectRenderedDetail(profileBoardID, c, "https://careers.example.net/job/123", Browser)
		if e != nil || len(p.EnrichmentFields) != 3 || p.EnrichmentFields[0] != "description" || p.EnrichmentFields[1] != "employment_type" || p.EnrichmentFields[2] != "job_location_type" {
			t.Fatal("actual registry selected browser detail rejected", e)
		}
	}
	if !found {
		t.Fatal("actual registry assignment missing")
	}
}
