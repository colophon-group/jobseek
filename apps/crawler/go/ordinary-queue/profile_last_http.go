package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func LastHTTPProvider(provider string) bool {
	return provider == "infor" || provider == "peoplesoft" || provider == "papa_johns" || provider == "unisante"
}

func lastHTTPMonitorEnrichment(config map[string]string) ([]string, error) {
	allowed := map[string]bool{}
	if config["crawler_type"] == "infor" {
		allowed["description"] = true
	}
	if config["crawler_type"] == "peoplesoft" {
		for _, field := range []string{"description", "employment_type", "job_location_type", "base_salary"} {
			allowed[field] = true
		}
	}
	return monitorEnrichmentFields(config, allowed)
}

func inspectLastHTTPMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := lastHTTPMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	o, err := api.LastHTTPOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if o.Provider == "unisante" {
		if _, err := unisanteMigrationConfig(config); err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.ListingURL())
}
