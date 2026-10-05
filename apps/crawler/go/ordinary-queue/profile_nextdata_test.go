package queue

import "testing"

func TestRenderedNextdataProfilesBindNavigationAndSeparateBrowserOwner(t *testing.T) {
	for _, fields := range []string{``, `,"fields":{"title":"title"}`} {
		c := profileConfig()
		c["crawler_type"], c["monitor_needs_browser"] = "nextdata", "1"
		c["metadata"] = `{"render":true,"wait":"domcontentloaded","timeout":120000,"path":"jobs","url_template":"https://example.com/jobs/{id}"` + fields + `}`
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || MonitorWorker(p) != Browser || monitorWorkerProfile(c) != Browser {
			t.Fatal("rendered inventory lost browser binding", err)
		}
		want := "nextdata.rendered-urls/v1"
		if fields != "" {
			want = "nextdata.rendered-items/v1"
		}
		if p.Profile != want {
			t.Fatal(p.Profile)
		}
		o, nav, err := RenderedNextdataMonitorOptions(c)
		if err != nil || o.BoardURL != c["board_url"] || nav["timeout"] != float64(120000) {
			t.Fatal("navigation controls lost", err)
		}
		c["metadata"] = `{"render":true,"wait":"load","timeout":120000,"path":"jobs","url_template":"https://example.com/jobs/{id}"` + fields + `}`
		other, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("navigation change retained binding", err)
		}
		c["monitor_needs_browser"] = "0"
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("simple owner adopted rendered inventory")
		}
	}
}

func TestNextdataProfilesBindSemanticConfigurationAndPaginationWitness(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "nextdata"
	c["metadata"] = `{"path":"jobs","url_template":"https://example.com/jobs/{id}","pagination":{"path":"pagination","page_count":"pages","page_param":"page"},"scraper_type":"json-ld"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "nextdata.embedded-urls/v1" {
		t.Fatal("URL inventory unavailable", err)
	}
	c["metadata"] = `{"scraper_type":"json-ld","pagination":{"page_param":"page","page_count":"pages","path":"pagination"},"url_template":"https://example.com/jobs/{id}","path":"jobs"}`
	other, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || other.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("semantic metadata changed binding", err)
	}
	o, err := NextdataMonitorOptions(c)
	if err != nil {
		t.Fatal(err)
	}
	page, err := o.PageURL(1)
	if err != nil {
		t.Fatal(err)
	}
	if !NextdataMonitorResourceMatches(p, c, page) || NextdataMonitorResourceMatches(p, c, "https://other.example/jobs?page=2") || NextdataMonitorResourceMatches(p, c, c["board_url"]+"?page=2&unbound=1") {
		t.Fatal("response page escaped configured authority")
	}
	c["metadata"] = `{"path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"name"},"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
	rich, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || rich.Profile != "nextdata.embedded-items/v1" || rich.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("rich configuration did not bind fields", err)
	}
}

func TestNextdataRefusesUnfinishedAuthorityAndInvalidOptions(t *testing.T) {
	for _, extra := range []string{`"render":true`, `"source":"browser"`, `"source_identity":{"provider":"heyjobs","tenant":"fixture","field":"id"}`, `"board_gone_statuses":[404]`, `"expected_hiring_organization":"Fixture"`, `"strict_path":"false"`, `"fields":{"title":{"unknown":"name"}}`, `"pagination":{"path":"pagination","page_count":"pages","concurrency":6}`, `"unknown":true`} {
		c := profileConfig()
		c["crawler_type"], c["metadata"] = "nextdata", `{"path":"jobs","url_template":"https://example.com/jobs/{id}",`+extra+`}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatalf("unproved option acquired owner: %s", extra)
		}
	}
}
