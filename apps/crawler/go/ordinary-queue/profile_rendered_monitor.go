package queue

import (
	"encoding/json"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

const domRenderedMonitorProfile = "dom.rendered-urls/v1"

func MonitorWorker(profile GreenhouseMonitorProfile) WorkerType {
	if profile.Profile == domRenderedMonitorProfile {
		return Browser
	}
	return Simple
}

func monitorWorkerProfile(config map[string]string) WorkerType {
	if config["crawler_type"] == "dom" && config["monitor_needs_browser"] == "1" {
		return Browser
	}
	return Simple
}

// Validate navigation and the existing single-page inventory parser separately,
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
	if err := validateRenderedNavigation(options); err != nil {
		return fail()
	}
	cloneMD := make(map[string]json.RawMessage, len(md))
	for key, value := range md {
		cloneMD[key] = value
	}
	for _, key := range []string{"browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers"} {
		delete(cloneMD, key)
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
