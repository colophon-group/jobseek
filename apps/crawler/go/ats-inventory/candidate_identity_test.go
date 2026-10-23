package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestCandidateIdentityMatchesFrozenPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_candidate_identity.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Family, URL, Normalized, Marker string
		Candidate                       inventoryCandidate
		Tenants                         []struct {
			Config map[string]any
			Key    *string
		}
	}
	if e = json.Unmarshal(raw, &cases); e != nil {
		t.Fatal(e)
	}
	registry, e := loadCompatibility()
	if e != nil {
		t.Fatal(e)
	}
	for i, c := range cases {
		url, e := normalizeBoardURL(c.URL)
		if e != nil || url != c.Normalized {
			t.Fatalf("Python normalized URL case %d: got %q want %q error %v", i, url, c.Normalized, e)
		}
		company := companyImpact{ATS: c.Family, Name: "Example GmbH", Slug: "example", URL: c.URL, ActiveJobs: 4, RemoteJobs: 2, LocationCount: 3, CountryCodes: []string{"CH", "US"}, LatestPostedAt: inventoryStringPointer("2026-10-01")}
		candidate, e := candidateFromImpact(company, registry)
		if e != nil || !reflect.DeepEqual(candidate, c.Candidate) {
			t.Fatalf("Python candidate identity case %d differs: %v", i, e)
		}
		for _, tenant := range c.Tenants {
			want := ""
			if tenant.Key != nil {
				want = *tenant.Key
			}
			if got := candidateTenantKey(c.Family, url, tenant.Config); got != want {
				t.Fatalf("Python candidate tenant case %d (%s) got %q want %q", i, c.Family, got, want)
			}
		}
		marker, e := candidateMarker(candidate.SourceKey, candidate.BoardURL)
		if e != nil || marker != c.Marker {
			t.Fatalf("Python candidate marker case %d differs", i)
		}
		parsed := parseCandidateMarkers(marker)
		if len(parsed) != 1 || parsed[0].SourceKey != candidate.SourceKey || parsed[0].BoardHash != candidate.boardURLHash() {
			t.Fatal("candidate marker cannot recover identity")
		}
	}
}
