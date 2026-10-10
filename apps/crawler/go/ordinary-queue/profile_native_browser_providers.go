package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func NativeBrowserProvider(provider string) bool {
	return provider == "darwinbox" || provider == "bytedance" || provider == "accenture"
}
func NativeBrowserProfile(profile string) bool {
	return profile == "darwinbox.session-items/v1" || profile == "bytedance.partition-items/v1" || profile == "accenture.http-items/v1"
}
func inspectNativeBrowserMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "1" || config["scraper_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "skip" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, e := monitorEnrichmentFields(config, map[string]bool{}); e != nil {
		return GreenhouseMonitorProfile{}, e
	}
	if config["crawler_type"] == "accenture" {
		_, _, err := api.AccentureHTTPOptions(config["board_url"], config["metadata"])
		if err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		return inspectURLOnlyMonitor(boardID, config, md, "accenture", "accenture.http-items/v1", "accenture", config["board_url"])
	}
	_, listing, e := api.NativeBrowserOptions(config["crawler_type"], config["board_url"], config["metadata"])
	if e != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	profile := "bytedance.partition-items/v1"
	if config["crawler_type"] == "darwinbox" {
		profile = "darwinbox.session-items/v1"
	}
	return inspectURLOnlyMonitor(boardID, config, md, config["crawler_type"], profile, config["crawler_type"], listing)
}
func NativeBrowserMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	if p.Provider == "accenture" {
		_, options, err := api.AccentureHTTPOptions(config["board_url"], config["metadata"])
		return err == nil && config["crawler_type"] == p.Provider && p.Profile == "accenture.http-items/v1" && p.Endpoint == config["board_url"] && (resource == p.Endpoint || resource == options.Endpoint)
	}
	_, listing, e := api.NativeBrowserOptions(p.Provider, config["board_url"], config["metadata"])
	return e == nil && NativeBrowserProfile(p.Profile) && p.Provider == config["crawler_type"] && p.Endpoint == listing && api.NativeBrowserResourceMatches(p.Provider, config["board_url"], config["metadata"], resource)
}

func apiNativeBrowserProfileResourceMatches(p GreenhouseMonitorProfile, resource string) bool {
	if p.Provider == "accenture" {
		return p.Profile == "accenture.http-items/v1" && (resource == p.Endpoint || resource == "https://www.accenture.com/api/accenture/"+api.AccentureFindJobs)
	}
	return NativeBrowserProfile(p.Profile) && api.NativeBrowserResourceMatches(p.Provider, p.Endpoint, "{}", resource)
}
