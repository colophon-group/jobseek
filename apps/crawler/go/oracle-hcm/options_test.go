package oraclehcm

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestCandidateRejectsUntrustedRoutes(t *testing.T) {
	for _, source := range []string{
		"http://acme.fa.eu2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001",
		"https://user@acme.fa.eu2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001",
		"https://acme.fa.eu2.oraclecloud.com:8443/hcmUI/CandidateExperience/en/sites/CX_1001",
		"https://acme.fa.eu2.oraclecloud.com.evil.example/hcmUI/CandidateExperience/en/sites/CX_1001",
		oracleBoard + "/other",
		oracleBoard + "/job/1/extra",
	} {
		if _, _, _, ok := Candidate(source, false); ok {
			t.Fatalf("untrusted route accepted %s", source)
		}
	}
}

func TestCandidateSupportsOracleDomainsAndPreview(t *testing.T) {
	for _, host := range []string{"acme.fa.eu2.oraclecloud.com", "evht.fa.ocs.oraclecloud.eu", "iaayey.fa.ocs.oraclecloud26.com", "jpmc.fa.oraclecloud.com"} {
		source := "https://" + host + "/hcmUI/CandidateExperience/en/sites/CX_1/requisitions/preview/240348971"
		h, s, id, ok := Candidate(source, true)
		if !ok || h != host || s != "CX_1" || id != "240348971" {
			t.Fatalf("supported identity lost %s %s %s %v", h, s, id, ok)
		}
	}
}

func TestOptionsRejectInvalidToleranceAndPartialMappings(t *testing.T) {
	for _, metadata := range []map[string]any{
		{"offset_overlap": 200}, {"offset_overlap": true}, {"offset_overlap": -1},
		{"total_count_tolerance": 1.5}, {"duplicate_row_tolerance": json.Number("1.0")},
		{"page_shortfall_tolerance": 200}, {"fields": map[string]any{"title": "Name"}},
		{"host": "oraclecloud.com.evil.example"}, {"organization_id": "1,offset=200"},
	} {
		if _, err := OptionsFromMetadata(oracleBoard, metadata); !errors.Is(err, ErrOptions) {
			t.Fatalf("unsupported metadata accepted %+v %v", metadata, err)
		}
	}
}

func TestOrganizationSourcesMustAgree(t *testing.T) {
	o, err := OptionsFromMetadata(oracleBoard+"?selectedOrganizationsFacet=42", nil)
	if err != nil || o.Organization != "42" {
		t.Fatalf("URL facet lost %+v %v", o, err)
	}
	_, err = OptionsFromMetadata(oracleBoard+"?selectedOrganizationsFacet=42", map[string]any{"organization_id": "43"})
	if !errors.Is(err, ErrOptions) {
		t.Fatalf("conflicting facet accepted %v", err)
	}
}

func TestLargeNumericIDsPreserved(t *testing.T) {
	const id = "123456789123456789123456789"
	got, ok := facetID(json.Number(id))
	if !ok || got != id {
		t.Fatalf("numeric identity rounded %s %v", got, ok)
	}
}
