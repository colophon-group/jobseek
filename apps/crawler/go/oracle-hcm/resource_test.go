package oraclehcm

import "testing"

func TestInventoryResourcesBindTenantSiteFacetAndBoundedOffset(t *testing.T) {
	o, err := OptionsFromMetadata("https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs", map[string]any{"offset_overlap": 10})
	if err != nil {
		t.Fatal(err)
	}
	for _, suffix := range []string{"", ",offset=190", ",selectedCategoriesFacet=engineering", ",selectedOrganizationsFacet=300000013747000,offset=380"} {
		if !o.ResourceMatches(o.Endpoint() + suffix) {
			t.Fatal("parser resource rejected", suffix)
		}
	}
	for _, suffix := range []string{",offset=0190", ",offset=200", ",offset=10070", ",offset=-190", ",offset=0", ",selectedCategoriesFacet=x&other=y", ",selectedCategoriesFacet=x#other", ",selectedCategoriesFacet=x,selectedOrganizationsFacet=y", ",offset=190,offset=380", ",selectedUnknownFacet=x"} {
		if o.ResourceMatches(o.Endpoint() + suffix) {
			t.Fatal("unbound resource accepted", suffix)
		}
	}
	if o.ResourceMatches("https://other.fa.em2.oraclecloud.com" + o.Endpoint()[len("https://fixture.fa.em2.oraclecloud.com"):]) {
		t.Fatal("foreign tenant accepted")
	}
}
