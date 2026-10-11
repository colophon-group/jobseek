package queue

import (
	"encoding/json"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

const domRenderedMonitorProfile = "dom.rendered-urls/v1"

func MonitorWorker(profile GreenhouseMonitorProfile) WorkerType {
	if NativeBrowserProfile(profile.Profile) || profile.Profile == apiSnifferBrowserProfile || profile.Profile == inlineRenderedMonitorProfile || RSSRenderedProfile(profile.Profile) || profile.Profile == dayforceMonitorProfile || profile.Profile == domRenderedMonitorProfile || profile.Profile == domRenderedRowsProfile || profile.Profile == "nextdata.rendered-items/v1" || profile.Profile == "nextdata.rendered-urls/v1" {
		return Browser
	}
	return Simple
}

func monitorWorkerProfile(config map[string]string) WorkerType {
	if (NativeBrowserProvider(config["crawler_type"]) || config["crawler_type"] == "api_sniffer" || config["crawler_type"] == "inline" || config["crawler_type"] == "dayforce" || config["crawler_type"] == "dom" || config["crawler_type"] == "nextdata" || config["crawler_type"] == "rss") && config["monitor_needs_browser"] == "1" {
		return Browser
	}
	return Simple
}

// Validate navigation and the existing inventory parser separately,
// binding the original canonical configuration rather than this parser clone.
func RenderedDOMMonitorOptions(config map[string]string) (dom.ListingConfig, map[string]any, error) {
	fail := func() (dom.ListingConfig, map[string]any, error) {
		return dom.ListingConfig{}, nil, ErrUnsupportedProfile
	}
	if monitorWorkerProfile(config) != Browser {
		return fail()
	}
	md, err := richProfileMetadata(config)
	if err != nil {
		return fail()
	}
	if md["prospective_board"] != nil && string(md["prospective_board"]) != "null" {
		return fail() // Original CareerCenter proof supports static rich rows only.
	}
	options := map[string]any{}
	for _, key := range []string{"render", "browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers", "proxy", "skip_ssl", "channel", "stealth", "headless", "persistent_context", "user_agent", "resource_policy", "transport_attempts", "encoding"} {
		if raw, ok := md[key]; ok {
			var v any
			if json.Unmarshal(raw, &v) != nil {
				return fail()
			}
			options[key] = v
		}
	}
	if err := validateRenderedNavigation(options, true); err != nil {
		return fail()
	}
	for _, key := range []string{"inactive_detail_states", "exclude_detail_selector", "onclick_selector", "script_json_links"} {
		if raw := md[key]; raw != nil && string(raw) != "null" {
			return fail()
		}
	}
	cloneMD := make(map[string]json.RawMessage, len(md))
	for key, value := range md {
		cloneMD[key] = value
	}
	for _, key := range []string{"browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers"} {
		delete(cloneMD, key)
	}
	if raw, exists := cloneMD["pagination"]; exists && string(raw) != "null" {
		// Whole-list totals newly admitted with pagination are static-only.
		// Preserve the previously qualified single-page rendered proof path.
		if total, exists := md["advertised_total"]; exists && string(total) != "null" {
			return fail()
		}
		var pagination map[string]json.RawMessage
		if json.Unmarshal(raw, &pagination) != nil || pagination == nil {
			return fail()
		}
		if flag, exists := pagination["browser"]; exists {
			var browser bool
			if json.Unmarshal(flag, &browser) != nil || browser {
				return fail()
			}
			delete(pagination, "browser")
		}
		// Browser-fetch tails still require a held-session contract. The admitted
		// mixed route renders only the root and uses verified HTTP for its tails.
		// Browser authority remains bound to the original profile and options;
		// only the pure URL/pagination parser receives this static clone.
		parsed, err := json.Marshal(pagination)
		if err != nil {
			return fail()
		}
		cloneMD["pagination"] = parsed
	}
	cloneMD["render"] = json.RawMessage(`false`)
	body, err := json.Marshal(cloneMD)
	if err != nil {
		return fail()
	}
	clone := cloneConfig(config)
	clone["monitor_needs_browser"], clone["metadata"] = "0", string(body)
	listing, err := directDOMMonitorOptions(clone)
	if err != nil {
		return fail()
	}
	return listing, options, nil
}
