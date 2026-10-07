package queue

import (
	"encoding/json"
	"net/url"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

const domDirectRowsProfile = "dom.direct-rows/v1"
const domRenderedRowsProfile = "dom.rendered-rows/v1"

func DOMMonitorUsesRichRows(profile string) bool {
	return profile == domDirectRowsProfile || profile == domRenderedRowsProfile || profile == "dom.proxy-rows/v1"
}
func DOMRichMonitorEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true})
}

func DOMMonitorOptions(config map[string]string) (dom.ListingConfig, error) {
	if monitorWorkerProfile(config) == Browser {
		listing, _, err := RenderedDOMMonitorOptions(config)
		return listing, err
	}
	return directDOMMonitorOptions(config)
}

func directDOMMonitorOptions(config map[string]string) (dom.ListingConfig, error) {
	parsed, parseErr := httpMonitorParsingConfig(config)
	if parseErr != nil {
		return dom.ListingConfig{}, parseErr
	}
	config = parsed
	md, err := richProfileMetadata(config)
	if err != nil {
		return dom.ListingConfig{}, err
	}
	if md["browser_backend"] != nil || md["routing_revision"] != nil {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	options := dom.Object{}
	keys := []string{"url_filter", "link_selector", "render", "proxy", "skip_ssl", "ssl_verify", "actions", "pagination", "transport_attempts", "request_headers", "encoding", "wait", "timeout", "headless", "channel", "stealth", "persistent_context", "user_agent", "wait_fallback", "resource_policy", "url_transform"}
	for _, key := range keys {
		if raw, ok := md[key]; ok {
			if (key == "url_filter" || key == "request_headers") && strings.HasPrefix(strings.TrimSpace(string(raw)), "{") {
				if _, err := profileMetadataFields(string(raw), nil); err != nil {
					return dom.ListingConfig{}, ErrUnsupportedProfile
				}
			}
			var value any
			d := json.NewDecoder(strings.NewReader(string(raw)))
			d.UseNumber()
			if err := d.Decode(&value); err != nil {
				return dom.ListingConfig{}, ErrUnsupportedProfile
			}
			options[key] = value
		}
	}
	listing, err := dom.ListingOptions(options, config["board_url"])
	if err != nil {
		return dom.ListingConfig{}, err
	}
	listing.RichRows, err = dom.RichRowsOptions(md["rich_rows"])
	if err == nil && listing.RichRows != nil && listing.RichRows.TotalSelector != "" && listing.Pagination != nil {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	return listing, err
}

func inspectDOMMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	u, err := url.Parse(config["board_url"])
	if err != nil || len(config["board_url"]) > 8192 || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" || !validHost(u.Hostname()) || u.Port() != "" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := FeedMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	listing, err := DOMMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	profile := "dom.direct-urls/v1"
	if monitorWorkerProfile(config) == Browser {
		profile = domRenderedMonitorProfile
	}
	if listing.RichRows != nil {
		if _, err := DOMRichMonitorEnrichment(config); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
		profile = domDirectRowsProfile
		if monitorWorkerProfile(config) == Browser {
			profile = domRenderedRowsProfile
		}
	}
	return inspectURLOnlyMonitor(boardID, config, md, "dom", profile, "dom", config["board_url"])
}

func DOMMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	c, err := DOMMonitorOptions(config)
	return err == nil && p.Provider == "dom" && c.ResourceMatches(p.Endpoint, resource)
}
