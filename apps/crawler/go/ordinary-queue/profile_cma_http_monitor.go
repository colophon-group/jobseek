package queue

import (
	_ "embed"
	"encoding/json"
	"reflect"
)

//go:embed cma_http_detail_monitor.json
var cmaHTTPDetailMonitorContract []byte

var cmaHTTPDetailMonitor = func() struct {
	Slug    string          `json:"slug"`
	URL     string          `json:"board_url"`
	Forward json.RawMessage `json:"forward_config"`
	Reverse json.RawMessage `json:"reverse_config"`
} {
	var c struct {
		Slug    string          `json:"slug"`
		URL     string          `json:"board_url"`
		Forward json.RawMessage `json:"forward_config"`
		Reverse json.RawMessage `json:"reverse_config"`
	}
	if json.Unmarshal(cmaHTTPDetailMonitorContract, &c) != nil || c.Slug != "cma-cgm-air-belgium" || c.URL != "https://www.airbelgium.com/jobs/" {
		panic("invalid committed CMA monitor/detail contract")
	}
	return c
}()

func selfIdentifyingDOMMonitorSource(config map[string]string, md map[string]json.RawMessage) bool {
	if selfIdentifyingDOMSource(config, md) || qualifiedFinalFragmentMonitor(config, md) || qualifiedFinalDetailTransition(config, md) {
		return true
	}
	if config["board_slug"] != cmaHTTPDetailMonitor.Slug || config["board_url"] != cmaHTTPDetailMonitor.URL || config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "1" || config["scraper_needs_browser"] != "0" {
		return false
	}
	var scraper string
	var actual, forward any
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "dom" || json.Unmarshal(md["scraper_config"], &actual) != nil || json.Unmarshal(cmaHTTPDetailMonitor.Forward, &forward) != nil || !reflect.DeepEqual(actual, forward) {
		return false
	}
	// The independently qualified HTTP detail replaces only the detail half
	// of this board's frozen contract. Authenticate the unchanged monitor
	// against its original policy; execution and ownership still bind the
	// actual current configuration. No arbitrary detail or monitor variation
	// inherits the old direct-rendered replacement permission.
	legacyMD := make(map[string]json.RawMessage, len(md))
	for key, value := range md {
		legacyMD[key] = value
	}
	legacyMD["scraper_config"] = cmaHTTPDetailMonitor.Reverse
	body, err := json.Marshal(legacyMD)
	if err != nil {
		return false
	}
	legacy := cloneConfig(config)
	legacy["metadata"], legacy["scraper_needs_browser"] = string(body), "1"
	return selfIdentifyingDOMSource(legacy, legacyMD)
}
