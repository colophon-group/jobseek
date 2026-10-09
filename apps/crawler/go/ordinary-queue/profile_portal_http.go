package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func PortalHTTPProvider(provider string) bool {
	return provider == "pageup" || provider == "infoniqa" || provider == "keka" || provider == "turbohire"
}

func portalMonitorEnrichment(config map[string]string) ([]string, error) {
	allowed := map[string]bool{}
	if config["crawler_type"] == "pageup" {
		allowed["description"] = true
	}
	return monitorEnrichmentFields(config, allowed)
}

func inspectPortalHTTPMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := portalMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if config["crawler_type"] == "keka" || config["crawler_type"] == "turbohire" {
		if err := feedRichDetailAssignment(config); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
	}
	o, err := api.PortalHTTPProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.Listing)
}

func portalHTTPMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	o, err := api.PortalHTTPProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil || resource != o.Listing || o.Provider != "keka" && o.Provider != "pageup" {
		return false
	}
	if disabled {
		return o.Provider == "keka" && (status == 301 || status == 302 || status == 307 || status == 308)
	}
	if status == 404 || status == 410 {
		return true
	}
	return false
}
