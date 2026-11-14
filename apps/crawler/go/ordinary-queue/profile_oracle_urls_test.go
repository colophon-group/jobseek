package queue

import "testing"

const oraclePublicURLPolicy = `{"url_allowlist":"^https://eeho\\.fa\\.us2\\.oraclecloud\\.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/[0-9]+$","url_transform":{"find":"^https://eeho\\.fa\\.us2\\.oraclecloud\\.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/([0-9]+)$","replace":"https://careers.oracle.com/en/job/\\1"},"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`

func TestOraclePublicURLPolicyPreservesIdentityAndRejectsBoundaryViolations(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "oracle_hcm"
	c["board_url"] = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/jobs"
	c["metadata"] = oraclePublicURLPolicy
	if _, err := InspectRichMonitor(profileBoardID, c); err != nil {
		t.Fatal("enabled Oracle policy rejected", err)
	}
	rules, err := OracleMonitorURLRules(c)
	if err != nil {
		t.Fatal(err)
	}
	source := "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/1234567890123456789"
	got, err := rules.Apply(source)
	if err != nil || got != "https://careers.oracle.com/en/job/1234567890123456789" {
		t.Fatal("Oracle identity rewrite differs", got, err)
	}
	for _, bad := range []string{source + "?foreign=1", source + "/extra", source + "\n", "https://foreign.example.com/job/123"} {
		if _, err := rules.Apply(bad); err == nil {
			t.Fatal("provider boundary violation accepted")
		}
	}
	for _, bad := range []string{`{"url_allowlist":""}`, `{"url_allowlist":"["}`, `{"url_transform":{"find":"(a)","replace":"https://example.com/\\2"}}`, `{"url_transform":{"find":"(a)","replace":"https://example.com/\\10"}}`, `{"url_transform":{"find":"a","replace":"https://example.com/{identity}"}}`, `{"url_transform":{"find":"a","replace":"https://example.com/{identity}","collision_policy":"prefer_source_pattern"}}`} {
		c["metadata"] = bad
		if _, err := OracleMonitorURLRules(c); err == nil {
			t.Fatal("unported or invalid policy admitted", bad)
		}
	}
}

func TestOracleURLFilterAdmissionAndRawSelection(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "oracle_hcm"
	c["board_url"] = "https://fixture.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"
	for _, filter := range []string{`"/job/[0-9]+$"`, `{"include":"/job/","exclude":"_ORA_DELETED$"}`, `null`} {
		c["metadata"] = `{"url_filter":` + filter + `,"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`
		if _, err := InspectRichMonitor(profileBoardID, c); err != nil {
			t.Fatal("valid Oracle filter rejected", filter, err)
		}
		rules, err := OracleMonitorURLRules(c)
		if err != nil {
			t.Fatal(err)
		}
		for _, id := range []string{"23538", "18037_300000876670395_ORA_DELETED"} {
			keep, err := rules.FilterURL(c["board_url"] + "/job/" + id)
			want := filter == `null` || id == "23538"
			if err != nil || keep != want {
				t.Fatal("filter differs from offline selection", filter, id, keep, err)
			}
		}
	}
	for _, filter := range []string{`"["`, `{"exclude":"["}`, `{"exclude":1}`, `{"unknown":"x"}`, `[]`} {
		c["metadata"] = `{"url_filter":` + filter + `}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("malformed Oracle filter admitted", filter)
		}
	}
}
