package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func BeisenMonitorOptions(config map[string]string) (api.BeisenBoard, error) {
	if config["crawler_type"] != "beisen" || config["monitor_needs_browser"] != "0" {
		return api.BeisenBoard{}, ErrUnsupportedProfile
	}
	return api.BeisenOptionsFromMetadata(config["board_url"], config["metadata"])
}
func inspectBeisenMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	b, err := BeisenMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := beisenMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "beisen", "beisen.portal-items/v1", b.Tenant, b.RootURL())
}
func beisenMonitorEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"description": true})
}
func BeisenMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	b, err := BeisenMonitorOptions(config)
	return err == nil && p.Provider == "beisen" && p.Profile == "beisen.portal-items/v1" && p.Endpoint == b.RootURL() && b.ResourceMatches(resource)
}
func BeisenMonitorPrimaryGone(config map[string]string, resource string, status int, disabled bool) bool {
	b, err := BeisenMonitorOptions(config)
	if err != nil {
		return false
	}
	if disabled {
		return resource == b.RootURL() && status == 200
	}
	if status != 404 && status != 410 {
		return false
	}
	return resource == b.RootURL() || resource == b.RootURL()+"Social" || resource == b.RootURL()+"index"
}
