package queue

import (
	"encoding/json"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func APISnifferMonitorOptions(config map[string]string) (apisniffer.Options, error) {
	if config["crawler_type"] != "api_sniffer" || config["monitor_needs_browser"] != "0" {
		return apisniffer.Options{}, ErrUnsupportedProfile
	}
	return apisniffer.OptionsFromMetadata(config["board_url"], config["metadata"])
}

// API monitors auto-select skip when they already expose fields. Only an
// explicit scraper enrichment list delegates data and schedules detail work.
func apiSnifferMonitorEnrichment(config map[string]string) ([]string, error) {
	o, err := APISnifferMonitorOptions(config)
	if err != nil {
		return nil, err
	}
	return o.Enrichment, nil
}

func inspectAPISnifferMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	o, err := APISnifferMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := apiSnifferMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "api_sniffer", "api_sniffer.http-items/v1", "api_sniffer", o.Endpoint)
}

func APISnifferMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, endpoint string) bool {
	o, err := APISnifferMonitorOptions(config)
	return err == nil && p.Provider == "api_sniffer" && p.Profile == "api_sniffer.http-items/v1" && p.Endpoint == o.Endpoint && o.ResourceMatches(endpoint)
}
