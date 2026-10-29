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
	parsed, err := apiSnifferURLParsingConfig(config)
	if err != nil {
		return apisniffer.BrowserReplayOptions{}, err
	}
	return apisniffer.BrowserReplayOptionsFromMetadata(parsed["board_url"], parsed["metadata"])
}

// APISnifferBrowserReplayMetadata carries only the validated network/parser
// metadata. The request fingerprint and canonical writer retain the complete
// original board configuration, including its URL identity policy.
func APISnifferBrowserReplayMetadata(config map[string]string) (json.RawMessage, error) {
	if _, err := APISnifferBrowserMonitorOptions(config); err != nil {
		return nil, err
	}
	parsed, err := apiSnifferURLParsingConfig(config)
	if err != nil {
		return nil, err
	}
	return json.RawMessage(parsed["metadata"]), nil
}

func APISnifferMonitorOptions(config map[string]string) (apisniffer.Options, error) {
	parsed, parseErr := httpMonitorParsingConfig(config)
	if parseErr != nil {
		return apisniffer.Options{}, parseErr
	}
	parsed, err := apiSnifferURLParsingConfig(parsed)
	if err != nil {
		return apisniffer.Options{}, err
	}
	if parsed["crawler_type"] != "api_sniffer" || parsed["monitor_needs_browser"] != "0" {
		return apisniffer.Options{}, ErrUnsupportedProfile
	}
	return apisniffer.OptionsFromMetadata(parsed["board_url"], parsed["metadata"])
}

func apiSnifferURLParsingConfig(config map[string]string) (map[string]string, error) {
	// Shared URL identity policy is validated and applied by the canonical
	// inventory writer for both HTTP and browser API inventories. Keep it out
	// of the network/parser-only clone, retaining the complete source binding.
	if _, err := FeedMonitorURLRules(config); err != nil {
		return nil, err
	}
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	if _, ok := md["url_transform"]; ok {
		delete(md, "url_transform")
		body, err := json.Marshal(md)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
		parsed := cloneConfig(config)
		parsed["metadata"] = string(body)
		return parsed, nil
	}
	return config, nil
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
		return err == nil && p.Provider == "api_sniffer" && p.Endpoint == config["board_url"] && (endpoint == p.Endpoint || endpoint == o.BootstrapURL || o.Inventory.ResourceMatches(endpoint) || o.LiveURLMatches(endpoint, o.Inventory.Method))
	}
	o, err := APISnifferMonitorOptions(config)
	return err == nil && p.Provider == "api_sniffer" && (p.Profile == "api_sniffer.http-items/v1" || p.Profile == "api_sniffer.proxy-http-items/v1") && p.Endpoint == o.Endpoint && (o.ResourceMatches(endpoint) || o.IsRequestHeaderRefreshRequest(apisniffer.Request{Method: "GET", URL: endpoint}))
}
