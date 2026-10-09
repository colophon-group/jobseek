package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func FinalHTTPProvider(provider string) bool {
	return provider == "paynet" || provider == "nowhiring" || provider == "fenbi" || provider == "wecruit" || provider == "curately" || provider == "inploi" || provider == "jobconvo"
}

func finalHTTPMonitorEnrichment(config map[string]string) ([]string, error) {
	allowed := map[string]bool{}
	if config["crawler_type"] == "inploi" {
		allowed["description"] = true
	}
	return monitorEnrichmentFields(config, allowed)
}

func inspectFinalHTTPProviderMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := finalHTTPMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if config["crawler_type"] != "inploi" && config["crawler_type"] != "jobconvo" {
		if err := feedRichDetailAssignment(config); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
	}
	o, err := api.FinalHTTPProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.ListingURL())
}

func finalHTTPProviderMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	if disabled || status != 404 && status != 410 {
		return false
	}
	o, err := api.FinalHTTPProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	return err == nil && (o.Provider == "nowhiring" || o.Provider == "jobconvo" || o.Provider == "curately" && o.ClientID == 0) && resource == o.ListingURL()
}
