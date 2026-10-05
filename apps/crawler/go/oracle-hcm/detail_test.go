package oraclehcm

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestDetailEndpointBindsAllowlistedTenant(t *testing.T) {
	for _, test := range []struct {
		source   string
		metadata map[string]any
	}{
		{source: "https://acme.fa.eu2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/123"},
		{source: "https://careers.example.com/jobs/123", metadata: map[string]any{"host": "acme.fa.eu2.oraclecloud.com", "site": "CX_1001"}},
	} {
		got, err := DetailEndpoint(test.source, test.metadata)
		want := `https://acme.fa.eu2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="123",siteNumber=CX_1001`
		if err != nil || got != want {
			t.Fatalf("endpoint %s %v", got, err)
		}
	}
}

func TestDetailEndpointRejectsUnboundVanityAndUnsafeIdentity(t *testing.T) {
	for _, source := range []string{"https://careers.example.com/jobs/123", "http://careers.example.com/jobs/123", "https://acme.fa.eu2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1/extra"} {
		if _, err := DetailEndpoint(source, nil); !errors.Is(err, ErrOptions) {
			t.Fatalf("unsafe detail accepted %s %v", source, err)
		}
	}
}

func TestProjectDetailDescriptionFallbackAndExtras(t *testing.T) {
	for _, description := range []string{"", "<p>Role description</p>"} {
		b, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"Title": "Engineer", "PrimaryLocation": "Zurich", "ExternalPostedStartDate": "2026-10-05", "JobSchedule": "Full time", "ExternalDescriptionStr": description, "OrganizationDescriptionStr": "<p>Organization</p>", "CorporateDescriptionStr": "Corporate", "ExternalQualificationsStr": "<p>Qualification</p>", "ExternalResponsibilitiesStr": " "}}})
		got, err := ProjectDetail(b)
		if err != nil {
			t.Fatal(err)
		}
		want := description
		if want == "" {
			want = "<p>Organization</p>"
		}
		if got["description"] != want || got["title"] != "Engineer" || got["date_posted"] != "2026-10-05" {
			t.Fatalf("detail fields %+v", got)
		}
		extra := got["extras"].(map[string]any)
		if extra["responsibilities"] != nil || !strings.Contains(extra["qualifications"].([]string)[0], "Qualification") {
			t.Fatalf("extras %+v", extra)
		}
	}
}

func TestProjectDetailDoesNotInventDescriptionForBlankRequisition(t *testing.T) {
	got, err := ProjectDetail([]byte(`{"items":[{"Title":"Engineer","ExternalDescriptionStr":"","OrganizationDescriptionStr":""}]}`))
	if err != nil || got["description"] != nil {
		t.Fatalf("blank description manufactured %+v %v", got, err)
	}
}
