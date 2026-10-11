package queue

import (
	"encoding/json"
	"strings"

	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
)

func OracleMonitorOptions(config map[string]string) (oracle.Options, error) {
	parsed, err := httpMonitorParsingConfig(config)
	if err != nil {
		return oracle.Options{}, err
	}
	config = parsed
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
	if _, err := OracleMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := oracleMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["proxy"]; ok && string(raw) != "true" && string(raw) != "false" && string(raw) != "null" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	o, err := OracleMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "oracle_hcm", "oracle_hcm.finder-items/v1", o.Site, o.Endpoint())
}

// Oracle's rich inventory owns title/location/liveness while its configured
// scraper supplies description and, on two enabled boards, employment type.
// The detail consumer retains its existing owner until independently admitted.
func oracleMonitorEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"description": true, "locations": true, "employment_type": true})
}

func monitorEnrichmentFields(config map[string]string, allowed map[string]bool) ([]string, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	var scraper string
	if raw, ok := md["scraper_type"]; ok && string(raw) != "null" && json.Unmarshal(raw, &scraper) != nil {
		return nil, ErrUnsupportedProfile
	}
	raw, configured := md["scraper_config"]
	if !configured {
		if scraper == "" {
			return []string{"description"}, nil
		}
		return nil, nil
	}
	if string(raw) == "null" {
		return nil, nil
	}
	options, err := profileMetadataFields(string(raw), nil)
	if err != nil {
		return nil, err
	}
	raw, present := options["enrich"]
	if !present || string(raw) == "null" {
		return nil, nil
	}
	var fields []string
	if json.Unmarshal(raw, &fields) != nil {
		return nil, ErrUnsupportedProfile
	}
	seen := map[string]bool{}
	for _, field := range fields {
		if seen[field] || !allowed[field] || scraper == "skip" {
			return nil, ErrUnsupportedProfile
		}
		seen[field] = true
	}
	return fields, nil
}

func OracleMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, endpoint string) bool {
	o, err := OracleMonitorOptions(config)
	return err == nil && p.Provider == "oracle_hcm" && (p.Profile == "oracle_hcm.finder-items/v1" || p.Profile == "oracle_hcm.proxy-finder-items/v1") && p.Endpoint == o.Endpoint() && o.ResourceMatches(endpoint)
}
