package queue

import "testing"

func TestJoinDetailRetainsConfiguredFieldsAndActualSourceBinding(t *testing.T) {
	c := joinMonitorConfig()
	c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"path":"props.pageProps.initialState.job","fields":{"title":"title","description":"schemaDescription || unifiedDescription || description","locations":["=Switzerland"]}}}`
	const source = "https://join.com/companies/fixture/123-engineer"
	p, err := InspectAPIDetail(profileBoardID, c, source, Simple)
	if err != nil || p.Profile != joinDetailProfile || p.SourceURL != source || p.Endpoint != source || p.Domain != "join.com" || len(p.JoinDetailConfig) != 2 {
		t.Fatalf("configured Join detail rejected: %+v %v", p, err)
	}
	owned, err := inspectAPIDetailOwnership(profileBoardID, c)
	if err != nil || owned.Domain != "*" || owned.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 {
		t.Fatal("independent configured detail lost its board binding", err)
	}
	for _, raw := range []string{"https://foreign.example/companies/fixture/123", "http://join.com/companies/fixture/123", "https://join.com/companies/fixture", "https://join.com/companies/fixture/123#fragment", "https://u:p@join.com/companies/fixture/123"} {
		if _, err := InspectAPIDetail(profileBoardID, c, raw, Simple); err == nil {
			t.Fatal("unsupported actual source admitted", raw)
		}
	}
	for _, options := range []string{`{"fields":{}}`, `{"path":"foreign","fields":{"title":"title"}}`, `{"path":"props.pageProps.initialState.job","fields":{"title":"foreign"}}`, `{"path":"props.pageProps.initialState.job","fields":{"title":"title"},"render":false}`} {
		c["metadata"] = `{"scraper_type":"nextdata","scraper_config":` + options + `}`
		if _, err := InspectAPIDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unported detail configuration admitted", options)
		}
	}
}
