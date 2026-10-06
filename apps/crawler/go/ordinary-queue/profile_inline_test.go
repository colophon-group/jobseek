package queue

import "testing"

func TestInlineProfileBindsExtractionIdentityAndAlternatePublicResources(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"], c["metadata"] = "inline", "https://example.com/jobs", `{"scraper_type":"skip","steps":[{"tag":"h2","field":"title"}],"fetch_urls":["https://example.com/jobs","https://example.net/representation"],"require_zero_proof":true}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "inline.document-items/v1" || MonitorWorker(p) != Simple {
		t.Fatal(err)
	}
	for _, u := range []string{"https://example.com/jobs", "https://example.net/representation"} {
		if !InlineMonitorResourceMatches(p, c, u) {
			t.Fatal("declared public resource lost")
		}
	}
	for _, u := range []string{"https://example.net/foreign", "https://other.com/jobs"} {
		if InlineMonitorResourceMatches(p, c, u) {
			t.Fatal("foreign resource gained authority")
		}
	}
	c["metadata"] = `{"steps":[{"tag":"h3","field":"title"}],"scraper_type":"skip"}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("step binding lost", err)
	}
	for _, m := range []string{`{"unknown":true}`, `{"render":true,"steps":[{"tag":"h2","field":"title"}]}`, `{"proxy":true,"steps":[{"tag":"h2","field":"title"}]}`, `{"steps":[{"tag":"h2","field":"title"}],"fetch_urls":[{"url":"https://example.com/jobs","headers":{"Authorization":"Bearer private"}}]}`, `{"require_zero_proof":true}`, `{"positions_per_listing":true}`, `{"include_hidden":"true"}`} {
		c["metadata"] = m
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported configuration gained authority")
		}
	}
}
