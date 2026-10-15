package queue

import "testing"

func TestRealJSONLDWorkdayRecoveryColdRetirement(t *testing.T) {
	for _, transport := range []string{"direct", "proxy"} {
		t.Run(transport, func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				metadata := `{"scraper_type":"json-ld","scraper_config":{"workday_fallback":{"company":"fixture","wd_instance":"wd5","site":"External","proxy":true}}}`
				if transport == "proxy" {
					metadata = `{"scraper_type":"json-ld","scraper_config":{"proxy":true,"workday_fallback":{"company":"fixture","wd_instance":"wd5","site":"External","proxy":false}}}`
				}
				return firstIndependentDetailFixtureAtBoard(t, metadata, "https://example.com/job/REQ100/engineer", "example.com", "https://example.com/careers")
			})
		})
	}
}
