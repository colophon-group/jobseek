package queue

import (
	"encoding/json"
	"testing"
)

// CSV publication retains a discovered sitemap even after changing providers.
// Exercise the full SQL/cache shape rather than reconstructing static CSV only.
func TestReconfiguredProvidersBindRetainedSitemap(t *testing.T) {
	for _, provider := range []string{"talentbrew", "oracle_hcm"} {
		t.Run(provider, func(t *testing.T) {
			c := profileConfig()
			c["crawler_type"] = provider
			md := map[string]any{"scraper_type": "json-ld", "recent_discovered_counts": []int{2, 2}, "suspect_streak": 0, "_monitor_config_fingerprint": "retained"}
			source := "https://example.com/job/1"
			if provider == "talentbrew" {
				c["board_url"] = "https://example.com/search-jobs"
			} else {
				c["domain"], c["throttle_key"] = "oracle_hcm", "oracle_hcm"
				c["board_url"] = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
				source = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1"
				md["scraper_type"], md["scraper_config"] = "oracle_hcm", map[string]any{"enrich": []string{"description", "employment_type", "job_location_type"}}
			}
			put := func() {
				body, err := json.Marshal(md)
				if err != nil {
					t.Fatal(err)
				}
				c["metadata"] = string(body)
			}
			put()
			baseline, err := InspectRichMonitor(profileBoardID, c)
			if err != nil {
				t.Fatal("static monitor rejected", err)
			}
			detail, err := inspectDetail(profileBoardID, c, source, Simple)
			if err != nil {
				t.Fatal("static detail rejected", err)
			}
			if provider == "oracle_hcm" && len(detail.EnrichmentFields) != 3 {
				t.Fatal("configured workplace enrichment lost")
			}
			md["sitemap_url"] = "https://old.example/sitemap.xml"
			put()
			admitted, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || admitted.Profile != baseline.Profile || admitted.Endpoint != baseline.Endpoint || admitted.EffectiveConfigSHA256 == baseline.EffectiveConfigSHA256 {
				t.Fatal("retained sitemap must be bound without changing the request", err)
			}
			projected, _, err := inspectMonitorConfigs(profileBoardID, c, cloneConfig(c))
			if err != nil || projected.EffectiveConfigSHA256 != admitted.EffectiveConfigSHA256 {
				t.Fatal("full SQL/cache projection rejected", err)
			}
			next, err := inspectDetail(profileBoardID, c, source, Simple)
			if err != nil || next.Profile != detail.Profile || next.Endpoint != detail.Endpoint || next.EffectiveBoardSHA256 == detail.EffectiveBoardSHA256 {
				t.Fatal("detail lost retained configuration binding", err)
			}
			md["sitemap_url"] = "https://other.example/sitemap.xml"
			put()
			changed, err := InspectRichMonitor(profileBoardID, c)
			if err != nil || changed.EffectiveConfigSHA256 == admitted.EffectiveConfigSHA256 {
				t.Fatal("retained sitemap changed without rebinding", err)
			}
			md["sitemap_url"] = 123
			put()
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("non-string retained sitemap admitted")
			}
			md["sitemap_url"] = "https://old.example/sitemap.xml"
			md["unported"] = true
			put()
			if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
				t.Fatal("unknown option admitted")
			}
		})
	}
}
