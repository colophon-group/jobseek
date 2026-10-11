package queue

import "testing"

func TestOracleProxyMonitorAndDetailRetainIndependentTransportAuthority(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "oracle_hcm", "oracle_hcm", "oracle_hcm"
	c["board_url"] = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
	c["metadata"] = `{"proxy":true,"scraper_type":"oracle_hcm","scraper_config":{"proxy":true,"enrich":["description"]}}`
	m, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || m.Profile != "oracle_hcm.proxy-finder-items/v1" || !ProfileRequiresProxy(m.Profile) || !OracleMonitorResourceMatches(m, c, m.Endpoint+",offset=200") {
		t.Fatal("configured Oracle proxy monitor rejected", m, e)
	}
	d, e := InspectAPIDetail(profileBoardID, c, c["board_url"]+"/job/123", Simple)
	if e != nil || d.Profile != oracleProxyDetailProfile || !ProfileRequiresProxy(d.Profile) || len(d.EnrichmentFields) != 1 {
		t.Fatal("separate Oracle proxy detail rejected", d, e)
	}
	c["metadata"] = `{"proxy":true,"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`
	plain, e := InspectAPIDetail(profileBoardID, c, c["board_url"]+"/job/123", Simple)
	if e != nil || plain.Profile != oracleDetailProfile || ProfileRequiresProxy(plain.Profile) || plain.EffectiveBoardSHA256 == d.EffectiveBoardSHA256 {
		t.Fatal("monitor proxy leaked into detail transport", e)
	}
	if OracleMonitorResourceMatches(m, c, "https://foreign.fa.em2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions") {
		t.Fatal("foreign tenant accepted")
	}
	c["metadata"] = `{"proxy":"enabled"}`
	if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
		t.Fatal("unvalidated proxy option accepted")
	}
}

func TestOracleConfiguredAPIEnrichmentPreservesLocationDelegation(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = "oracle_hcm", "oracle_hcm", "oracle_hcm"
	c["board_url"] = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
	c["metadata"] = `{"scraper_type":"api_sniffer","scraper_config":{"enrich":["description","locations"]}}`
	p, e := InspectRichMonitor(profileBoardID, c)
	fields, fieldErr := oracleMonitorEnrichment(c)
	if e != nil || fieldErr != nil || p.Profile != "oracle_hcm.finder-items/v1" || len(fields) != 2 {
		t.Fatal("configured Oracle description/location detail delegation rejected", p, e)
	}
}
