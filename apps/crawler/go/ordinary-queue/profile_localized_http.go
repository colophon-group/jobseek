package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func LocalizedHTTPProvider(provider string) bool {
	return provider == "talemetry" || provider == "prospective" || provider == "kipt"
}

type localizedResource interface{ ResourceMatches(string) bool }

func localizedHTTPOptions(config map[string]string) (string, string, localizedResource, error) {
	switch config["crawler_type"] {
	case "talemetry":
		o, e := api.TalemetryOptionsFromMetadata(config["board_url"], config["metadata"])
		return o.Profile(), o.PageURL(1), o, e
	case "prospective":
		o, e := api.ProspectiveOptionsFromMetadata(config["board_url"], config["metadata"])
		return "prospective.localized-items/v1", o.BoardURL, o, e
	case "kipt":
		o, e := api.KIPTOptionsFromMetadata(config["board_url"], config["metadata"])
		return "kipt.bulletin-items/v1", o.BoardURL, o, e
	}
	return "", "", nil, ErrUnsupportedProfile
}
func inspectLocalizedHTTPMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, e := monitorEnrichmentFields(config, map[string]bool{}); e != nil {
		return GreenhouseMonitorProfile{}, e
	}
	if config["crawler_type"] != "talemetry" {
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
	}
	profile, endpoint, _, e := localizedHTTPOptions(config)
	if e != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, config["crawler_type"], profile, config["crawler_type"], endpoint)
}
func localizedHTTPResourceMatches(p GreenhouseMonitorProfile, config map[string]string, raw string) bool {
	profile, endpoint, o, e := localizedHTTPOptions(config)
	return e == nil && p.Provider == config["crawler_type"] && p.Profile == profile && p.Endpoint == endpoint && o.ResourceMatches(raw)
}
