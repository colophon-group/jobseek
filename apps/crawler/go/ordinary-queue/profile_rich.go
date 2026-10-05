package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
	"net/url"
	"regexp"
	"strings"
)

var ashbyURLToken = regexp.MustCompile(`jobs\.ashbyhq\.com/([\pL\pN_-]+)`)
var leverURLToken = regexp.MustCompile(`jobs\.(?:eu\.)?lever\.co/([\pL\pN_-]+)`)
var leverEURegion = regexp.MustCompile(`(?:api|jobs)\.eu\.lever\.co/`)
var richProviderToken = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$`)

func richProfileMetadata(config map[string]string) (map[string]json.RawMessage, error) {
	if config["crawler_type"] == "greenhouse" {
		return profileMetadata(config["metadata"])
	}
	allowed := make(map[string]bool, len(greenhouseMetadataFields)+4)
	for key, value := range greenhouseMetadataFields {
		allowed[key] = value
	}
	switch config["crawler_type"] {
	case "ashby":
		allowed["org"] = true // Legacy aliases are retained, never selected as tokens.
		allowed["blast_radius_floor"] = true
	case "lever":
		allowed["company"] = true
		allowed["region"] = true
	case "recruitee":
		allowed["slug"], allowed["api_base"] = true, true
		allowed["company"], allowed["company_slug"] = true, true
	case "pinpoint":
		allowed["slug"] = true
	case "personio":
		allowed["slug"], allowed["language"], allowed["backfill_languages"] = true, true, true
	case "rss":
		allowed["preset"], allowed["feed_url"] = true, true
		allowed["variant"] = true
	case "workday":
		for _, key := range []string{"company", "wd_instance", "site", "all_sites", "sites", "search_text", "split_facet", "facet_union", "tenant", "board", "board_id", "employer", "site_id", "instance", "job_board", "board_slug", "ssl_verify", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "smartrecruiters":
		for _, key := range []string{"canonical_identity", "canonical_job_id_url_template", "language_preference", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "join":
		for _, key := range []string{"slug", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "dom":
		for _, key := range []string{"url_filter", "link_selector", "render", "proxy", "skip_ssl", "ssl_verify", "actions", "pagination", "transport_attempts", "request_headers", "encoding", "wait", "timeout", "headless", "channel", "stealth", "persistent_context", "user_agent", "wait_fallback", "resource_policy", "browser_backend", "routing_revision", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}

	case "sitemap":
		for _, key := range []string{"sitemap_url", "xml_attempts", "url_filter", "proxy", "render", "skip_ssl", "ssl_verify", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "workable":
		for _, key := range []string{"delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "oracle_hcm":
		for _, key := range oracle.ConfigKeys {
			allowed[key] = true
		}
		for _, key := range []string{"proxy", "url_allowlist", "url_transform", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	case "api_sniffer":
		for _, key := range apisniffer.ConfigKeys {
			allowed[key] = true
		}
		for _, key := range []string{"delist_threshold", "drop_threshold", "blast_radius_floor"} {
			allowed[key] = true
		}
	default:
		return nil, ErrUnsupportedProfile
	}
	return profileMetadataFields(config["metadata"], allowed)
}

// InspectRichMonitor admits only the existing complete API/skip contracts.
// It reuses the Greenhouse authority/interval/transport validation, then binds
// the original provider configuration. It never fetches the configured URL.
func InspectRichMonitor(boardID string, config map[string]string) (GreenhouseMonitorProfile, error) {
	if config["crawler_type"] == "greenhouse" {
		return InspectGreenhouseMonitor(boardID, config)
	}
	md, err := richProfileMetadata(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if config["crawler_type"] == "personio" {
		return inspectPersonioRich(boardID, config, md)
	}
	if config["crawler_type"] == "rss" {
		return inspectRSSRich(boardID, config, md)
	}
	if config["crawler_type"] == "workday" {
		return inspectWorkdayMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "api_sniffer" {
		return inspectAPISnifferMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "oracle_hcm" {
		return inspectOracleMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "join" {
		return inspectJoinMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "dom" {
		return inspectDOMMonitor(boardID, config, md)
	}

	if config["crawler_type"] == "sitemap" {
		return inspectSitemapMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "smartrecruiters" || config["crawler_type"] == "workable" {
		return inspectAPIMonitor(boardID, config, md)
	}
	var token, region string
	var tenantEndpoint string
	if config["crawler_type"] == "recruitee" || config["crawler_type"] == "pinpoint" {
		tenantEndpoint, err = richTenantEndpoint(config, md)
		if err != nil {
			return GreenhouseMonitorProfile{}, err
		}
	}
	if tenantEndpoint != "" {
		// These providers select slug/api_base, not the legacy token aliases.
		token = "provider-token"
	} else {
		if raw, exists := md["token"]; exists && json.Unmarshal(raw, &token) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if token == "" {
			pattern := ashbyURLToken
			if config["crawler_type"] == "lever" {
				pattern = leverURLToken
			}
			if match := pattern.FindStringSubmatch(config["board_url"]); match != nil {
				token = match[1]
				ignored := map[string]bool{"api": true, "js": true, "css": true, "assets": true, "posting-api": config["crawler_type"] == "ashby", "v0": config["crawler_type"] == "lever"}
				if ignored[token] {
					token = ""
				}
			}
		}
	}
	if !richProviderToken.MatchString(token) || token == "." || token == ".." {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if config["crawler_type"] == "lever" {
		if raw, exists := md["region"]; exists && json.Unmarshal(raw, &region) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if region == "" && leverEURegion.MatchString(config["board_url"]) {
			region = "eu"
		}
	}
	// Admission retains the original values for binding. Only the validation
	// copy supplies a canonical token and drops provider-specific aliases.
	validation := cloneConfig(config)
	validation["crawler_type"] = "greenhouse"
	validationMD := make(map[string]json.RawMessage)
	for key, value := range md {
		if greenhouseMetadataFields[key] {
			validationMD[key] = value
		}
	}
	if tenantEndpoint != "" {
		// Python skip monitors ignore leftover detail options unless an
		// explicit nonempty enrich list delegates fields to detail scraping.
		// Keep the original options in the binding while validating that no
		// detail work is required (for example Ergon's retained DOM steps).
		if raw, exists := md["scraper_config"]; exists {
			var options map[string]json.RawMessage
			if json.Unmarshal(raw, &options) != nil {
				return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
			}
			if raw, exists := options["enrich"]; exists {
				var fields []json.RawMessage
				if json.Unmarshal(raw, &fields) != nil || len(fields) != 0 {
					return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
				}
			}
			validationMD["scraper_config"] = json.RawMessage("null")
		}
	}
	// The common validator checks authority and transport policy. Provider
	// tokens have their own path-component contract, including dots and spaces.
	validationMD["token"], _ = json.Marshal("provider-token")
	body, err := json.Marshal(validationMD)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	validation["metadata"] = string(body)
	profile, err := InspectGreenhouseMonitor(boardID, validation)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if raw, exists := md["blast_radius_floor"]; exists && strings.TrimSpace(string(raw)) != "null" {
		var floor float64
		if json.Unmarshal(raw, &floor) != nil || floor < 0 || floor > 1 {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	stable, err := stableGreenhouseConfig(config, md)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	body, err = json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	digest := sha256.Sum256(body)
	profile.EffectiveConfigSHA256 = hex.EncodeToString(digest[:])
	profile.SnapshotSHA256 = configDigest(config)
	profile.Provider, profile.Region = config["crawler_type"], region
	profile.Token = token
	profile.Profile = profile.Provider + ".token-skip/v1"
	switch profile.Provider {
	case "ashby":
		profile.Endpoint = "https://api.ashbyhq.com/posting-api/job-board/" + url.PathEscape(token) + "?includeCompensation=true"
	case "lever":
		host := "api.lever.co"
		if region == "eu" {
			host = "api.eu.lever.co"
		}
		profile.Endpoint = "https://" + host + "/v0/postings/" + url.PathEscape(token) + "?limit=100&skip=0"
	case "recruitee":
		profile.Endpoint, profile.Profile = tenantEndpoint, "recruitee.api-skip/v1"
	case "pinpoint":
		profile.Endpoint, profile.Profile = tenantEndpoint, "pinpoint.slug-skip/v1"
	}
	return profile, nil
}
