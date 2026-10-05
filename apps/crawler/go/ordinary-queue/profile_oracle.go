package queue

import (
	"encoding/json"
	"strings"

	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
)

func OracleMonitorOptions(config map[string]string) (oracle.Options, error) {
	if config["crawler_type"] != "oracle_hcm" || config["monitor_needs_browser"] != "0" {
		return oracle.Options{}, ErrUnsupportedProfile
	}
	var md map[string]any
	d := json.NewDecoder(strings.NewReader(config["metadata"]))
	d.UseNumber()
	if d.Decode(&md) != nil {
		return oracle.Options{}, ErrUnsupportedProfile
	}
	return oracle.OptionsFromMetadata(config["board_url"], md)
}

func inspectOracleMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	// Most Oracle boards delegate description refresh to their detail scraper.
	// The shared rich writer currently owns complete content and cannot adopt
	// that field-mask/scheduling contract until its enrichment path is ported.
	var scraper string
	if raw, ok := md["scraper_type"]; ok && string(raw) != "null" {
		if json.Unmarshal(raw, &scraper) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	if scraper == "" && md["scraper_config"] == nil {
		// Python auto-selects Oracle description enrichment in this case.
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["scraper_config"]; ok && string(raw) != "null" {
		options, err := profileMetadataFields(string(raw), nil)
		if err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if raw, ok := options["enrich"]; ok && string(raw) != "null" {
			var fields []json.RawMessage
			if json.Unmarshal(raw, &fields) != nil || len(fields) != 0 {
				return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
			}
		}
	}
	if raw, ok := md["proxy"]; ok && string(raw) != "false" && string(raw) != "null" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	o, err := OracleMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "oracle_hcm", "oracle_hcm.finder-items/v1", o.Site, o.Endpoint())
}

func OracleMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, endpoint string) bool {
	o, err := OracleMonitorOptions(config)
	return err == nil && p.Provider == "oracle_hcm" && p.Profile == "oracle_hcm.finder-items/v1" && p.Endpoint == o.Endpoint() && o.ResourceMatches(endpoint)
}
