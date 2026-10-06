package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

var inlineMetadataKeys = []string{"steps", "render", "defaults", "defaults_by_title", "include_hidden", "exclude_titles", "exclude_title_regex", "exclude_description_regex", "preserve_single_location", "description_from_title", "positions_per_listing", "synthetic_identity_field", "item_boundary", "item_boundary_tag", "section_start", "section_end", "source_identity_selector", "source_identity_attribute", "source_identity_regex", "source_url_selector", "source_url_attribute", "valid_through_regex", "valid_through_patterns", "valid_through_format", "exclude_expired", "require_zero_proof", "empty_selector", "empty_text", "nonempty_selector", "empty_requires_no_jobs", "fetch_url", "fetch_urls", "fetch_contains", "fetch_json_path", "transport_attempts", "transient_403", "detail_click_selector", "detail_content_selector", "detail_identity_selector", "detail_identity_attribute", "detail_identity_regex", "enrich", "delist_threshold", "drop_threshold", "blast_radius_floor", "proxy", "skip_ssl", "ssl_verify", "wait", "timeout", "actions", "stealth", "headless", "channel", "persistent_context", "user_agent", "wait_fallback", "resource_policy", "browser_backend", "routing_revision"}

func InlineMonitorOptions(config map[string]string) (api.InlineMonitorOptions, error) {
	parsed, parseErr := httpMonitorParsingConfig(config)
	if parseErr != nil {
		return api.InlineMonitorOptions{}, parseErr
	}
	config = parsed
	if config["crawler_type"] != "inline" || config["monitor_needs_browser"] != "0" {
		return api.InlineMonitorOptions{}, ErrUnsupportedProfile
	}
	return api.InlineMonitorOptionsFromMetadata(config["board_url"], config["metadata"])
}
func inspectInlineMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	o, err := InlineMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err = inlineMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "inline", "inline.document-items/v1", "inline", o.BoardURL)
}
func inlineMonitorEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"description": true, "locations": true, "employment_type": true})
}
func InlineMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	o, err := InlineMonitorOptions(config)
	return err == nil && p.Provider == "inline" && (p.Profile == "inline.document-items/v1" || p.Profile == "inline.proxy-document-items/v1") && p.Endpoint == o.BoardURL && o.ResourceMatches(resource)
}
