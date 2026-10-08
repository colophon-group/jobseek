package queue

import (
	"encoding/json"
	"net/url"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func SmallProvider(provider string) bool {
	return provider == "jobbank104" || provider == "cnstaff" || provider == "seamlesshiring"
}
func inspectSmallProviderMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := monitorEnrichmentFields(config, map[string]bool{}); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if err := feedRichDetailAssignment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	o, err := api.SmallProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.ListingURL())
}
func smallProviderMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	if config["crawler_type"] == "seamlesshiring" || disabled || status != 404 && status != 410 {
		return false
	}
	o, e := api.SmallProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if e != nil || !o.ResourceMatches(resource) {
		return false
	}
	u, e := url.Parse(resource)
	if e != nil {
		return false
	}
	u.RawQuery = ""
	return u.String() == o.ListingURL()
}
