package queue

import "testing"

func TestJazzHRStrictTenantAndImmutableRequestBinding(t *testing.T) {
	for index, row := range []struct {
		url, md  string
		accepted bool
		tenant   string
	}{
		{"https://fixture.applytojob.com/apply", `{"scraper_type":"json-ld"}`, true, "fixture"},
		{"https://fixture.applytojob.com:443/apply/jobs/details/A1", `{"tenant":" FIXTURE ","scraper_type":"json-ld"}`, true, "fixture"},
		{"https://example.com/careers", `{"tenant":"configured","scraper_type":"json-ld"}`, true, "configured"},
		{"https://fixture.applytojob.com", `{"tenant":"different"}`, false, ""},
		{"https://fixture.applytojob.com", `{"tenant":null}`, false, ""},
		{"https://www.applytojob.com/apply", `{}`, false, ""},
		{"https://user:private@fixture.applytojob.com/apply", `{}`, false, ""},
		{"https://fixture.applytojob.com/unreviewed", `{}`, false, ""},
		{"https://fixture.applytojob.com", `{"tenant":"first","tenant":"second"}`, false, ""},
	} {
		c := profileConfig()
		c["crawler_type"], c["domain"], c["throttle_key"] = "jazzhr", "jazzhr", "jazzhr"
		c["board_url"], c["metadata"] = row.url, row.md
		p, err := InspectRichMonitor(profileBoardID, c)
		if (err == nil) != row.accepted || err == nil && (p.Token != row.tenant || p.Endpoint != "https://"+row.tenant+".applytojob.com/apply/jobs") {
			t.Fatal("provider identity differs", index, p, err)
		}
	}
}
