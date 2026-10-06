package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func MokahrMonitorOptions(config map[string]string) (api.MokahrOptions, error) {
	if config["crawler_type"] != "mokahr" || config["monitor_needs_browser"] != "0" {
		return api.MokahrOptions{}, ErrUnsupportedProfile
	}
	return api.MokahrOptionsFromMetadata(config["board_url"], config["metadata"])
}
func AlmaMonitorOptions(config map[string]string) (api.AlmaOptions, error) {
	if config["crawler_type"] != "almacareer" || config["monitor_needs_browser"] != "0" {
		return api.AlmaOptions{}, ErrUnsupportedProfile
	}
	return api.AlmaOptionsFromMetadata(config["board_url"], config["metadata"])
}
func EightfoldMonitorOptions(config map[string]string) (api.EightfoldOptions, error) {
	if config["crawler_type"] != "eightfold" || config["monitor_needs_browser"] != "0" {
		return api.EightfoldOptions{}, ErrUnsupportedProfile
	}
	return api.EightfoldOptionsFromMetadata(config["board_url"], config["metadata"])
}

func providerBatchEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"description": true})
}

func inspectProviderBatchMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if _, e := providerBatchEnrichment(config); e != nil {
		return GreenhouseMonitorProfile{}, e
	}
	provider := config["crawler_type"]
	var name, endpoint string
	switch provider {
	case "mokahr":
		o, e := MokahrMonitorOptions(config)
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		name, endpoint = "mokahr.encrypted-items/v1", o.Partitions[0].PageURL
	case "almacareer":
		o, e := AlmaMonitorOptions(config)
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		// These widgets hydrate complete descriptions in their monitor.
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		name, endpoint = "almacareer.graphql-items/v1", o.RootURL()
	case "eightfold":
		o, e := EightfoldMonitorOptions(config)
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if _, e := FeedMonitorURLRules(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		if _, e := stableEightfoldWatermark(md["pcsx_watermark"]); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		name, endpoint = "eightfold.pcsx-sitemap/v1", o.SitemapURL
	default:
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, provider, name, provider, endpoint)
}

func ProviderBatchMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	switch p.Provider {
	case "mokahr":
		o, e := MokahrMonitorOptions(config)
		return e == nil && p.Profile == "mokahr.encrypted-items/v1" && p.Endpoint == o.Partitions[0].PageURL && o.ResourceMatches(resource)
	case "almacareer":
		o, e := AlmaMonitorOptions(config)
		return e == nil && p.Profile == "almacareer.graphql-items/v1" && p.Endpoint == o.RootURL() && o.ResourceMatches(resource)
	case "eightfold":
		o, e := EightfoldMonitorOptions(config)
		return e == nil && p.Profile == "eightfold.pcsx-sitemap/v1" && p.Endpoint == o.SitemapURL && o.ResourceMatches(resource)
	}
	return false
}

func ProviderBatchMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	if config["crawler_type"] == "mokahr" {
		o, e := MokahrMonitorOptions(config)
		if e != nil {
			return false
		}
		for _, p := range o.Partitions {
			if resource == p.PageURL {
				return status == 404 && !disabled || status == 200 && disabled
			}
		}
	}
	if config["crawler_type"] == "almacareer" {
		o, e := AlmaMonitorOptions(config)
		return e == nil && resource == o.ScriptURL() && status == 404 && !disabled
	}
	return false
}

// Configuration remains bound even as the monitor advances its runtime state.
// Materialize the same defaults before/after a first watermark so the first
// successful crawl cannot invalidate its installed plan or independent details.
func stableEightfoldWatermark(raw json.RawMessage) (json.RawMessage, error) {
	fields := map[string]json.RawMessage{}
	if len(raw) > 0 && string(raw) != "null" {
		var e error
		fields, e = profileMetadataFields(string(raw), map[string]bool{"max_ts": true, "last_full_at": true, "last_incremental_at": true, "enabled": true, "extra": true, "interval_days": true, "auto_full_crawl": true})
		if e != nil {
			return nil, e
		}
	}
	var interval any = json.Number("7")
	if raw, ok := fields["interval_days"]; ok {
		var b bool
		if json.Unmarshal(raw, &b) == nil && b {
			interval = true
		} else {
			var n json.Number
			if json.Unmarshal(raw, &n) == nil {
				if value, e := n.Int64(); e == nil && value > 0 {
					interval = n
				}
			}
		}
	}
	auto := true
	if raw, ok := fields["auto_full_crawl"]; ok {
		var value bool
		if json.Unmarshal(raw, &value) == nil && string(raw) != "null" {
			auto = value
		}
	}
	body, e := json.Marshal(map[string]any{"interval_days": interval, "auto_full_crawl": auto})
	return body, e
}
