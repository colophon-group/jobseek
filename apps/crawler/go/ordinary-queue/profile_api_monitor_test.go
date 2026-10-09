package queue

import (
	"encoding/json"
	"testing"
)

func apiMonitorConfig(provider string) map[string]string {
	c := profileConfig()
	c["crawler_type"], c["domain"], c["throttle_key"] = provider, provider, provider
	c["board_url"] = "https://careers.smartrecruiters.com/fixture"
	if provider == "workable" {
		c["board_url"] = "https://apply.workable.com/fixture"
	}
	c["metadata"] = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
	c["scraper_needs_browser"] = "1"
	return c
}

func TestAPIMonitorURLOnlyProfilesBindDetailConfiguration(t *testing.T) {
	for _, provider := range []string{"smartrecruiters", "workable"} {
		c := apiMonitorConfig(provider)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != provider+".api-urls/v1" || p.Token != "fixture" {
			t.Fatalf("URL-only monitor rejected separate browser details: %+v %v", p, err)
		}
		c["metadata"] = `{"token":"explicit","scraper_type":"skip"}`
		q, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || q.Token != "explicit" || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("explicit provider token/config generation not bound", err)
		}
		for _, md := range []string{`{"token":"../fixture"}`, `{"proxy":"enabled"}`, `{"unknown":true}`, `{"blast_radius_floor":2}`, `{"blast_radius_floor":"0.5"}`} {
			c["metadata"] = md
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("unimplemented monitor configuration admitted", md)
			}
		}
		c["metadata"] = `{"scraper_type":"skip"}`
		c["monitor_needs_browser"] = "1"
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("browser monitor admitted as direct API")
		}
	}
	for _, md := range []string{`{"canonical_identity":"invalid"}`, `{"canonical_identity":"job-v1","canonical_job_id_url_template":"https://example.com/{job_id}"}`, `{"canonical_job_id_url_template":"https://example.com/{jobId}"}`} {
		c := apiMonitorConfig("smartrecruiters")
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("requisition identity admitted to URL-only writer")
		}
	}
}

func TestAPIMonitorResourceBinding(t *testing.T) {
	for _, provider := range []string{"smartrecruiters", "workable"} {
		p, err := InspectRichMonitor(profileBoardID, apiMonitorConfig(provider))
		if err != nil {
			t.Fatal(err)
		}
		good := []string{p.Endpoint}
		bad := []string{"http://apply.workable.com/fixture/jobs.md", "https://evil.example/jobs", p.Endpoint + "#fragment"}
		if provider == "smartrecruiters" {
			good = append(good, "https://api.smartrecruiters.com/v1/companies/fixture/postings?offset=100&limit=100")
			for _, q := range []string{"limit=100&offset=-100", "limit=100&offset=01", "limit=100&offset=1", "limit=100&offset=50100", "limit=100&offset=0&offset=100", "limit=100&offset=0&secret=x", "limit=100&offset=0&bad=%zz"} {
				bad = append(bad, "https://api.smartrecruiters.com/v1/companies/fixture/postings?"+q)
			}
		} else {
			good = append(good, "https://apply.workable.com/fixture/llms.txt", "https://apply.workable.com/fixture/jobs.md", "https://www.workable.com/api/accounts/fixture")
			bad = append(bad, "https://apply.workable.com/other/jobs.md", "https://apply.workable.com:443/fixture/jobs.md", p.Endpoint+"?token=other")
		}
		for _, u := range good {
			if !initialMonitorResourceMatches(p, u) {
				t.Fatal("actual provider page/fallback excluded", u)
			}
		}
		for _, u := range bad {
			if initialMonitorResourceMatches(p, u) {
				t.Fatal("foreign or malformed resource accepted", u)
			}
		}
		// The existing ownership document/codec carries the new profile without
		// changing its version or the legacy routing projection.
		doc := testOwnershipDocument(t)
		doc.Members = []ownershipMember{{profileBoardID, p.CompanyID, p.Domain, Monitor, Simple, p.Profile, p.EffectiveConfigSHA256, apiMonitorConfig(provider)}}
		body, digest := testOwnershipBody(t, doc)
		plan, err := decodeOwnership(body, digest)
		var projection ownershipProjectionDocument
		if err != nil || json.Unmarshal([]byte(plan.ProjectionJSON()), &projection) != nil || projection.Members[profileBoardID] != p.Domain {
			t.Fatal("existing ownership codec lost API URL monitor", err)
		}
	}
}
