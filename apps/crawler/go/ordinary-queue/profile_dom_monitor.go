package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
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
	if md["browser_backend"] != nil || md["routing_revision"] != nil || md["bot_protection"] != nil {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	options := dom.Object{}
	if raw := md["rich_rows"]; raw != nil {
		options["rich_rows"] = raw // Preserve ordered replacement mappings.
	}
	keys := []string{"inactive_detail_states", "exclude_detail_selector", "onclick_selector", "script_json_links", "include_board_url", "require_jsonld_jobposting", "advertised_total", "empty_states", "empty_selector", "empty_text", "url_filter", "link_selector", "render", "proxy", "skip_ssl", "ssl_verify", "actions", "pagination", "transport_attempts", "request_headers", "retry_statuses", "encoding", "wait", "timeout", "headless", "channel", "stealth", "persistent_context", "user_agent", "wait_fallback", "resource_policy", "url_transform"}
	keys = append(keys, dom.ListingProviderKeys...)
	keys = append(keys, "my_job_shop")
	keys = append(keys, "title_matched_url_scan", "require_pdf_text", "require_unexpired_pdf")
	keys = append(keys, "fingerprint_response")
	for _, key := range keys {
		if raw, ok := md[key]; ok {
			if (key == "url_filter" || key == "request_headers" || key == "retry_statuses") && strings.HasPrefix(strings.TrimSpace(string(raw)), "{") {
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
	if listing.Pagination != nil && listing.Pagination.Partitions != nil && (monitorWorkerProfile(config) == Browser || listing.RichRows != nil || listing.ScriptLinks != nil || listing.OnclickSelector != "" || listing.Proofs != nil || listing.RequireJSONLD || listing.IncludeBoardURL || listing.HasDetailFilters() || listing.TitleScan != nil || listing.PDFProof != nil || listing.ResponseFingerprintType != "") {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	if err == nil && listing.RichRows != nil && (listing.ScriptLinks != nil || listing.OnclickSelector != "") {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	richEmptyPagination := listing.RichRows != nil && listing.Pagination != nil && listing.Proofs != nil && listing.Proofs.TotalPattern == nil && len(listing.Proofs.EmptyStates) > 0
	if err == nil && listing.RichRows != nil && (listing.Proofs != nil && listing.ProviderProof == nil && !richEmptyPagination || listing.IncludeBoardURL || listing.RequireJSONLD) {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	if err == nil && listing.RichRows != nil && listing.RichRows.TotalSelector != "" && listing.Pagination != nil {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	if err == nil && listing.ProviderProof != nil && (listing.RichRows == nil || listing.RichRows.TotalSelector == "" || listing.Proofs == nil || len(listing.Proofs.EmptyStates) == 0 || listing.Proofs.TotalSelector != "" || listing.Pagination != nil) {
		return dom.ListingConfig{}, ErrUnsupportedProfile
	}
	if err == nil {
		listing.FetchURL, err = domMonitorFetchURL(config, md, listing)
	}
	return listing, err
}

func inspectDOMMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	u, err := url.Parse(config["board_url"])
	if err != nil || len(config["board_url"]) > 8192 || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" && !(config["monitor_needs_browser"] == "1" && selfIdentifyingDOMMonitorSource(config, md)) || !validHost(u.Hostname()) || u.Port() != "" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := FeedMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if _, qualified := qualifiedDOMHTTPPaginationSources[domHTTPPaginationConfigFingerprint(config)]; qualified && !DOMMonitorHasQualifiedHTTPPagination(boardID, config) {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, present := md["tupu360_listing"]; present {
		if string(raw) != "true" || !Tupu360Monitor(config) {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if _, err := DOMRichMonitorEnrichment(config); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
		return inspectURLOnlyMonitor(boardID, config, md, "dom", domDirectRowsProfile, "dom", config["board_url"])
	}
	listing, err := DOMMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if listing.ResponseFingerprintType != "" {
		var scraper string
		if listing.ResponseFingerprintType != "application/pdf" || json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "pdf" {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	profile := "dom.direct-urls/v1"
	if monitorWorkerProfile(config) == Browser {
		profile = domRenderedMonitorProfile
	}
	if listing.RichRows != nil || listing.ScriptLinks.Rich() {
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
	if Tupu360Monitor(config) {
		u, err := url.Parse(resource)
		base, _ := url.Parse(config["board_url"])
		if err != nil || p.Provider != "dom" || u.Scheme != base.Scheme || u.Host != base.Host || u.Path != base.Path || u.RawPath != "" || u.User != nil || u.Fragment != "" {
			return false
		}
		values, err := url.ParseQuery(u.RawQuery)
		return err == nil && len(values) == 2 && len(values["offset"]) == 1 && len(values["max"]) == 1 && values.Get("max") == "100" && tupuMonitorOffset.MatchString(values.Get("offset"))
	}
	c, err := DOMMonitorOptions(config)
	return err == nil && p.Provider == "dom" && (c.FetchURL != "" && resource == c.FetchURL || c.FetchURL == "" && c.ResourceMatches(p.Endpoint, resource))
}

var tupuMonitorOffset = regexp.MustCompile(`^(?:0|[1-9][0-9]{0,3})$`)

// The provider-specific static parser uses the existing direct rich-row writer.
// It ignores no unqualified transport or alternate parser options.
func Tupu360Monitor(config map[string]string) bool {
	if config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "0" || !api.Tupu360ListingURL(config["board_url"]) {
		return false
	}
	allowed := map[string]bool{"tupu360_listing": true, "job_filter": true, "url_filter": true, "url_allowlist": true, "url_transform": true, "scraper_type": true, "scraper_config": true, "_monitor_config_fingerprint": true, "suspect_streak": true, "recent_discovered_counts": true, "_confirmed_drop_candidate": true, "delist_threshold": true, "drop_threshold": true, "blast_radius_floor": true}
	md, err := profileMetadataFields(config["metadata"], allowed)
	return err == nil && string(md["tupu360_listing"]) == "true"
}
