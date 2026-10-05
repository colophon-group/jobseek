package queue

import (
	"strings"
	"testing"
)

const httpAPITestMetadata = `{"scraper_type":"api_sniffer","scraper_config":{"api_url":"https://api.example.net/jobs/{item_id}","url_pattern":"[?&]itemId=(?P<item_id>[^&#]+)","json_path":"job","fields":{"title":"title","description":"description","locations":"locations"},"enrich":["description","locations","employment_type","job_location_type","date_posted","base_salary"]}}`

func TestHTTPAPIDetailAdmissionBindsActualURLAndWildcardWithoutInventedID(t *testing.T) {
	c := apiDetailConfig("api_sniffer")
	c["crawler_type"] = "dom"
	c["metadata"] = httpAPITestMetadata
	source := "https://careers.example.net/position?itemId=123"
	p, err := InspectHTTPAPIDetail(profileBoardID, c, source, Simple)
	if err != nil || p.Profile != httpAPIDetailProfile || p.Endpoint != "https://api.example.net/jobs/123" || p.Domain != "careers.example.net" || len(p.EnrichmentFields) != 6 {
		t.Fatal("actual API detail refused", p, err)
	}
	owner, err := inspectDetailOwnership(profileBoardID, c)
	if err != nil || owner.Profile != p.Profile || owner.Domain != "*" || owner.Endpoint != "" || owner.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 {
		t.Fatal("wildcard detail admission invented a resource", owner, err)
	}
	c["metadata"] = strings.Replace(c["metadata"], "jobs/{item_id}", "openings/{item_id}", 1)
	changed, err := InspectHTTPAPIDetail(profileBoardID, c, source, Simple)
	if err != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
		t.Fatal("endpoint change failed to fence")
	}
	for _, source := range []string{"https://careers.example.net/job/123", "https://user:secret@careers.example.net/?itemId=123", "https://careers.example.net:444/?itemId=123", "file:///123"} {
		if _, err := InspectHTTPAPIDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unsafe/unresolved detail admitted")
		}
	}
	for _, options := range []string{`{"api_url":"https://api.example.net/{id}","fields":{"title":"title"},"render":true}`, `{"api_url":"https://api.example.net/{id}","fields":{"title":"title"},"auth_request":{"api_url":"https://auth.example.net/","method":"DELETE","method":"GET","header_fields":{"Authorization":"token"}}}`} {
		c["metadata"] = `{"scraper_type":"api_sniffer","scraper_config":` + options + `}`
		if _, err := InspectHTTPAPIDetail(profileBoardID, c, "https://careers.example.net/job/123", Simple); err == nil {
			t.Fatal("unported or ambiguous auth admitted")
		}
	}
}
func TestRealHTTPAPIDetailOwnershipAndColdRetirement(t *testing.T) {
	metadata := `{"scraper_type":"api_sniffer","scraper_config":{"api_url":"https://api.example.net/{id}","fields":{"title":"title","description":"description"}}}`
	t.Run("ownership", func(t *testing.T) {
		testIndependentDetailOwnsPosting(t, firstIndependentDetailFixture(t, metadata, "https://careers.example.net/job/123", "careers.example.net"), httpAPIDetailProfile, "careers.example.net")
	})
	t.Run("cold-retirement", func(t *testing.T) {
		testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
			return firstIndependentDetailFixture(t, metadata, "https://careers.example.net/job/123", "careers.example.net")
		})
	})
}
