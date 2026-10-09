package queue

import (
	"encoding/json"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const apiSnifferBrowserProfile = "api_sniffer.browser-items/v1"

func APISnifferBrowserMonitorOptions(config map[string]string) (apisniffer.BrowserReplayOptions, error) {
	if config["crawler_type"] != "api_sniffer" || config["monitor_needs_browser"] != "1" {
		return apisniffer.BrowserReplayOptions{}, ErrUnsupportedProfile
	}
	return apisniffer.BrowserReplayOptionsFromMetadata(config["board_url"], config["metadata"])
}

func APISnifferMonitorOptions(config map[string]string) (apisniffer.Options, error) {
	parsed, parseErr := httpMonitorParsingConfig(config)
	if parseErr != nil {
		return apisniffer.Options{}, parseErr
	}
	// Shared URL identity policy is validated and applied by the canonical
	// inventory writer. Keep it out of the network/parser-only clone.
	if _, err := FeedMonitorURLRules(config); err != nil {
		return apisniffer.Options{}, err
	}
	md, err := profileMetadataFields(parsed["metadata"], nil)
	if err != nil {
		return apisniffer.Options{}, err
	}
	if _, ok := md["url_transform"]; ok {
		delete(md, "url_transform")
		body, err := json.Marshal(md)
		if err != nil {
			return apisniffer.Options{}, ErrUnsupportedProfile
		}
		parsed = cloneConfig(parsed)
		parsed["metadata"] = string(body)
	}
	config = parsed
	if config["crawler_type"] != "api_sniffer" || config["monitor_needs_browser"] != "0" {
		return apisniffer.Options{}, ErrUnsupportedProfile
	}
	return apisniffer.OptionsFromMetadata(config["board_url"], config["metadata"])
}

// API monitors auto-select skip when they already expose fields. Only an
// explicit scraper enrichment list delegates data and schedules detail work.
func apiSnifferMonitorEnrichment(config map[string]string) ([]string, error) {
	if config["monitor_needs_browser"] == "1" {
		o, err := APISnifferBrowserMonitorOptions(config)
		return o.Inventory.Enrichment, err
	}
	o, err := APISnifferMonitorOptions(config)
	if err != nil {
		return nil, err
	}
	return o.Enrichment, nil
}

func inspectAPISnifferMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if _, err := FeedMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if config["monitor_needs_browser"] == "1" {
		if _, err := APISnifferBrowserMonitorOptions(config); err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		return inspectURLOnlyMonitor(boardID, config, md, "api_sniffer", apiSnifferBrowserProfile, "api_sniffer", config["board_url"])
	}
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
	if p.Profile == apiSnifferBrowserProfile {
		o, err := APISnifferBrowserMonitorOptions(config)
		return err == nil && p.Provider == "api_sniffer" && p.Endpoint == config["board_url"] && (endpoint == p.Endpoint || o.Inventory.ResourceMatches(endpoint))
	}
	o, err := APISnifferMonitorOptions(config)
	return err == nil && p.Provider == "api_sniffer" && (p.Profile == "api_sniffer.http-items/v1" || p.Profile == "api_sniffer.proxy-http-items/v1") && p.Endpoint == o.Endpoint && o.ResourceMatches(endpoint)
}
