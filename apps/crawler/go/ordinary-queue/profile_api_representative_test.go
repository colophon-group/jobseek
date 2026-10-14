package queue

import (
	"strings"
	"testing"
)

func TestOracleDetailOwnershipUsesCanonicalTenantForVanityBoard(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "oracle_hcm"
	c["board_url"] = "https://careers.example.net/"
	c["metadata"] = `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1001","scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`
	owner, err := inspectAPIDetailOwnership(profileBoardID, c)
	if err != nil || owner.Profile != oracleDetailProfile || owner.Domain != "*" || !strings.Contains(owner.Endpoint, "siteNumber=CX_1001") {
		t.Fatal("vanity monitor tenant identity lost", err)
	}
	actual, err := InspectAPIDetail(profileBoardID, c, "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/123", Simple)
	if err != nil || actual.EffectiveBoardSHA256 != owner.EffectiveBoardSHA256 || len(actual.EnrichmentFields) != 1 || actual.EnrichmentFields[0] != "description" {
		t.Fatal("actual detail binding differs", err)
	}
	c["metadata"] = strings.Replace(c["metadata"], `"enrich":["description"]`, `"host":"other.fa.em2.oraclecloud.com","site":"CX_2","enrich":["description"]`, 1)
	override, err := inspectAPIDetailOwnership(profileBoardID, c)
	if err != nil || !strings.HasPrefix(override.Endpoint, "https://other.fa.em2.oraclecloud.com/") || !strings.Contains(override.Endpoint, "siteNumber=CX_2") || override.EffectiveBoardSHA256 == owner.EffectiveBoardSHA256 {
		t.Fatal("detail override or fingerprint lost", err)
	}
	c["metadata"] = `{"host":"untrusted.example.net","site":"CX_1","scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`
	if _, err := inspectAPIDetailOwnership(profileBoardID, c); err == nil {
		t.Fatal("unsafe representative tenant accepted")
	}
}

func TestEmptyJOINNextdataMappingPreservesOriginalNoRequestProfile(t *testing.T) {
	for _, metadata := range []string{`{"scraper_type":"nextdata"}`, `{"scraper_type":"nextdata","scraper_config":null}`, `{"scraper_type":"nextdata","scraper_config":{}}`, `{"scraper_type":"nextdata","scraper_config":{"fields":{}}}`} {
		c := profileConfig()
		c["crawler_type"] = "join"
		c["board_url"] = "https://join.com/companies/native"
		c["metadata"] = metadata
		owner, err := inspectAPIDetailOwnership(profileBoardID, c)
		if err != nil || owner.Profile != joinDetailProfile {
			t.Fatal("empty mapping not supported", err)
		}
		actual, err := InspectAPIDetail(profileBoardID, c, "https://join.com/companies/native/123-engineer", Simple)
		if err != nil || actual.EffectiveBoardSHA256 != owner.EffectiveBoardSHA256 {
			t.Fatal("actual no-request binding differs", err)
		}
	}
}
