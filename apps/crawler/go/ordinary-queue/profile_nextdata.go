package queue

import (
	"encoding/json"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func NextdataMonitorOptions(config map[string]string) (apisniffer.NextdataOptions, error) {
	if config["crawler_type"] != "nextdata" || config["monitor_needs_browser"] != "0" {
		return apisniffer.NextdataOptions{}, ErrUnsupportedProfile
	}
	return apisniffer.NextdataOptionsFromMetadata(config["board_url"], config["metadata"])
}

func inspectNextdataMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	o, err := NextdataMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := oracleMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	profile := "nextdata.embedded-items/v1"
	if len(o.Fields) == 0 {
		profile = "nextdata.embedded-urls/v1"
	}
	return inspectURLOnlyMonitor(boardID, config, md, "nextdata", profile, "nextdata", o.BoardURL)
}

func NextdataMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	o, err := NextdataMonitorOptions(config)
	return err == nil && p.Provider == "nextdata" && p.Endpoint == o.BoardURL && o.ResourceMatches(resource)
}
