package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"strings"
)

const dayforceMonitorProfile = "dayforce.session-search/v1"

func inspectDayforceMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "1" || config["scraper_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "skip" || feedRichDetailAssignment(config) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	for _, flag := range []string{"proxy", "skip_ssl", "render", "ssl_verify"} {
		if raw, exists := md[flag]; exists {
			var v bool
			if json.Unmarshal(raw, &v) != nil || v != (flag == "render" || flag == "ssl_verify") {
				return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
			}
		}
	}
	if raw, exists := md["actions"]; exists && strings.TrimSpace(string(raw)) != "[]" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	board, _, err := api.DayforceOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "dayforce", dayforceMonitorProfile, board.Tenant+"/"+board.Portal, board.ListingURL())
}
