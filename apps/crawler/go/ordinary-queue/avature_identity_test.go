package queue

import (
	"encoding/json"
	"testing"
)

func TestAvatureSettlementConfirmsBoundIdentityWithoutRewritingOwnership(t *testing.T) {
	for _, mode := range []string{"valid", "foreign-provider", "foreign-host", "changed-portal", "operator-field", "missing-portal", "missing-listing", "canonical-drift"} {
		t.Run(mode, func(t *testing.T) {
			config := profileConfig()
			config["crawler_type"], config["board_url"] = "avature", "https://acme.avature.net/careers/SearchJobs"
			config["metadata"] = `{"listing_url":"https://acme.avature.net/careers/SearchJobs","portal_id":"4","scraper_type":"skip"}`
			var current map[string]any
			json.Unmarshal([]byte(config["metadata"]), &current)
			patch := map[string]any{"listing_url": "https://acme.avature.net/careers/SearchJobs", "portal_id": "4"}
			switch mode {
			case "foreign-provider":
				config["crawler_type"] = "seek"
			case "foreign-host":
				patch["listing_url"] = "https://other.avature.net/careers/SearchJobs"
			case "changed-portal":
				patch["portal_id"] = "5"
			case "operator-field":
				patch["scraper_type"] = "dom"
			case "missing-portal":
				delete(patch, "portal_id")
			case "missing-listing":
				delete(patch, "listing_url")
			case "canonical-drift":
				current["portal_id"] = "5"
			}
			if err := validateAvatureIdentityUpdate(config, current, patch); (err == nil) != (mode == "valid") {
				t.Fatal(mode, err)
			}
		})
	}
	for _, metadata := range []string{`{"listing_url":"https://acme.avature.net/careers/SearchJobs","scraper_type":"skip"}`, `{"portal_id":"4","scraper_type":"skip"}`, `{"listing_url":"https://acme.avature.net/careers/SearchJobs","portal_id":4,"scraper_type":"skip"}`, `{"listing_url":"https://acme.avature.net/careers/SearchJobs/","portal_id":"4","scraper_type":"skip"}`} {
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = "avature", "https://acme.avature.net/careers/SearchJobs", metadata
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unbound learned identity admitted")
		}
	}
}
