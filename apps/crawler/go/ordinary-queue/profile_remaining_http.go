package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func RemainingHTTPProvider(provider string) bool {
	return provider == "johdi" || provider == "jobdiva" || provider == "headhunter"
}

func remainingHTTPEnrichment(config map[string]string) ([]string, error) {
	allowed := map[string]bool{}
	if config["crawler_type"] == "headhunter" {
		for _, key := range []string{"description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary"} {
			allowed[key] = true
		}
	}
	return monitorEnrichmentFields(config, allowed)
}

func inspectRemainingHTTPMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, e := remainingHTTPEnrichment(config); e != nil {
		return GreenhouseMonitorProfile{}, e
	}
	o, e := api.RemainingHTTPOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if e != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.ListingURL())
}
