package queue

import (
	"encoding/json"
	"errors"
	"testing"
)

var apiDetailCases = []struct{ name, source, domain, endpoint, profile string }{
	{"smartrecruiters", "https://jobs.smartrecruiters.com/Fixture/123-engineer", "jobs.smartrecruiters.com", "https://api.smartrecruiters.com/v1/companies/Fixture/postings/123-engineer", smartRecruitersDetailProfile},
	{"workable", "https://apply.workable.com/fixture/j/ABC123/", "apply.workable.com", "https://apply.workable.com/api/v2/accounts/fixture/jobs/ABC123", workableDetailProfile},
}

func apiDetailConfig(name string) map[string]string {
	c := jsonldDetailConfig()
	c["metadata"] = `{"scraper_type":"` + name + `","selector":"a.job","render":true}`
	return c
}

func TestAPIDetailBindsActualProviderRouteWithoutAdoptingLegacyMonitor(t *testing.T) {
	for _, tc := range apiDetailCases {
		t.Run(tc.name, func(t *testing.T) {
			c := apiDetailConfig(tc.name)
			p, err := InspectAPIDetail(jsonldBoardID, c, tc.source, Simple)
			if err != nil || p.Profile != tc.profile || p.Endpoint != tc.endpoint || p.Domain != tc.domain {
				t.Fatal("wrong API route", p, err)
			}
			binding, err := inspectDetailOwnership(jsonldBoardID, c)
			if err != nil || binding.Domain != "*" || binding.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 {
				t.Fatal("ownership differs from actual route", err)
			}
			metadata, _ := profileMetadataFields(c["metadata"], nil)
			stable, _ := stableJSONLDConfig(c, metadata)
			doc := testOwnershipDocument(t)
			doc.Details = []ownershipDetail{{BoardID: jsonldBoardID, Domain: "*", Profile: p.Profile, Worker: Simple, CompanyID: p.CompanyID, EffectiveConfigHash: p.EffectiveBoardSHA256, Config: stable}}
			body, hash := testOwnershipBody(t, doc)
			plan, err := decodeOwnership(body, hash)
			if err != nil {
				t.Fatal(err)
			}
			var projection ownershipProjectionDocument
			if json.Unmarshal([]byte(plan.ProjectionJSON()), &projection) != nil || projection.Members[jsonldBoardID] != "" || projection.Details[jsonldBoardID] != "*" {
				t.Fatal("detail changed legacy monitor ownership")
			}
			c["metadata"] = `{"scraper_type":"` + tc.name + `","selector":"changed"}`
			changed, err := InspectAPIDetail(jsonldBoardID, c, tc.source, Simple)
			if err != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
				t.Fatal("board mutation retained authority", err)
			}
		})
	}
}

func TestAPIDetailRefusesUnimplementedConfigAndForeignRoutes(t *testing.T) {
	for _, tc := range apiDetailCases {
		for _, options := range []string{`{"proxy":true}`, `{"render":true}`, `{"ssl_verify":false}`, `{"defaults":{"title":"override"}}`, `{"fallback":["dom"]}`, `{"proxy":true,"proxy":false}`} {
			if tc.name == "workable" && options == `{"proxy":true}` {
				continue
			}
			c := apiDetailConfig(tc.name)
			c["metadata"] = `{"scraper_type":"` + tc.name + `","scraper_config":` + options + `}`
			if _, err := InspectAPIDetail(jsonldBoardID, c, tc.source, Simple); !errors.Is(err, ErrUnsupportedProfile) {
				t.Fatal("unsupported pipeline admitted", tc.name, options, err)
			}
		}
		for _, source := range []string{"https://foreign.example/job/1", "http://jobs.smartrecruiters.com/Fixture/1", "https://u:p@apply.workable.com/fixture/j/ABC123/", "https://apply.workable.com:444/fixture/j/ABC123/"} {
			if _, err := InspectAPIDetail(jsonldBoardID, apiDetailConfig(tc.name), source, Simple); err == nil {
				t.Fatal("foreign route admitted", source)
			}
		}
	}
	c := apiDetailConfig("workable")
	c["metadata"] = `{"scraper_type":"workable","scraper_config":{"token":"alternate"}}`
	p, err := InspectAPIDetail(jsonldBoardID, c, apiDetailCases[1].source, Simple)
	if err != nil || p.Endpoint != "https://apply.workable.com/api/v2/accounts/alternate/jobs/ABC123" || p.APITokenOverride != "alternate" {
		t.Fatal("token override changed", p, err)
	}
}

func TestRealAPIDetailColdRetirementPreservesCanonicalDeadline(t *testing.T) {
	for _, tc := range apiDetailCases {
		t.Run(tc.name, func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				return firstIndependentDetailFixture(t, apiDetailConfig(tc.name)["metadata"], tc.source, tc.domain)
			})
		})
	}
}

func TestRealAPIDetailOwnsPostingAndRetainsLegacyMonitor(t *testing.T) {
	for _, tc := range apiDetailCases {
		t.Run(tc.name, func(t *testing.T) {
			p := firstIndependentDetailFixture(t, apiDetailConfig(tc.name)["metadata"], tc.source, tc.domain)
			testIndependentDetailOwnsPosting(t, p, tc.profile, tc.domain)
		})
	}
}
