package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
)

var workableBoardToken = regexp.MustCompile(`apply\.workable\.com/([\pL\pN_-]+)`)

// URL-only inventories retain separately scheduled details. Canonical
// SmartRecruiters configurations use the existing rich durable-identity writer.
func inspectAPIMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
	provider := config["crawler_type"]
	var metadata map[string]any
	if json.Unmarshal([]byte(config["metadata"]), &metadata) != nil {
		return fail()
	}
	var token, endpoint string
	profile := provider + ".api-urls/v1"
	if provider == "smartrecruiters" {
		options, err := smartrecruiters.OptionsFromMetadata(config["board_url"], metadata)
		if err != nil {
			return fail()
		}
		if options.Identity != "" || options.Template != nil {
			if feedRichDetailAssignment(config) != nil {
				return fail()
			}
			profile = "smartrecruiters.canonical-items/v1"
		}
		token, endpoint = options.Token, smartrecruiters.ListURL(options.Token)+"?limit=100&offset=0"
	} else {
		if raw, ok := md["proxy"]; ok && string(raw) != "true" && string(raw) != "false" && string(raw) != "null" {
			return fail()
		}
		if raw, ok := md["token"]; ok && string(raw) != "null" {
			if json.Unmarshal(raw, &token) != nil {
				return fail()
			}
		}
		if token == "" {
			if match := workableBoardToken.FindStringSubmatch(config["board_url"]); match != nil {
				token = match[1]
				if map[string]bool{"api": true, "v2": true, "v3": true, "js": true, "css": true, "assets": true, "accounts": true, "jobs": true}[token] {
					token = ""
				}
			}
		}
		if !richProviderToken.MatchString(token) || strings.ContainsAny(token, ". ") {
			return fail()
		}
		endpoint = "https://apply.workable.com/api/v3/accounts/" + token + "/jobs"
	}
	return inspectURLOnlyMonitor(boardID, config, md, provider, profile, token, endpoint)
}

// Reuse the existing configuration/transport authority and URL-only lifecycle
// for inventory parsers whose details are scheduled separately.
func inspectURLOnlyMonitor(boardID string, config map[string]string, md map[string]json.RawMessage, provider, profile, token, endpoint string) (GreenhouseMonitorProfile, error) {
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
	if string(md["proxy"]) == "true" {
		if proxy, ok := httpMonitorProxyProfiles[profile]; ok && config["monitor_needs_browser"] == "0" {
			profile = proxy
		} else if !ProfileRequiresProxy(profile) {
			return fail()
		}
	}
	var lifecycle map[string]any
	decoder := json.NewDecoder(strings.NewReader(config["metadata"]))
	decoder.UseNumber()
	if decoder.Decode(&lifecycle) != nil {
		return fail()
	}
	if _, err := workdayDelistThreshold(lifecycle["delist_threshold"]); err != nil {
		return fail()
	}
	if _, err := workdayLifecycleSetting(lifecycle["drop_threshold"], 0.3); err != nil {
		return fail()
	}
	if raw, exists := md["blast_radius_floor"]; exists && strings.TrimSpace(string(raw)) != "null" {
		var floor float64
		if json.Unmarshal(raw, &floor) != nil || floor < 0 || floor > 1 {
			return fail()
		}
	}
	if config["scraper_needs_browser"] != "0" && config["scraper_needs_browser"] != "1" {
		return fail()
	}
	validation := cloneConfig(config)
	// Wecruit landing pages use a fragment-only SPA route. Provider validation
	// binds the explicit API origin and tenant; preserve the original URL in
	// the immutable profile while removing its fragment only for this validator.
	if provider == "wecruit" || provider == "jobdiva" {
		if u, err := url.Parse(validation["board_url"]); err == nil {
			u.Fragment, u.RawFragment = "", ""
			validation["board_url"] = u.String()
		}
	}
	// JazzHR's strict first-party identity permits the explicit default TLS port.
	// Normalize only this reused validator input; immutable binding keeps the
	// original configured URL and the provider endpoint has no explicit port.
	if provider == "jazzhr" || provider == "gupy" {
		if u, err := url.Parse(validation["board_url"]); err == nil && u.Port() == "443" {
			u.Host = u.Hostname()
			validation["board_url"] = u.String()
		}
	}
	validation["crawler_type"], validation["scraper_needs_browser"] = "greenhouse", "0"
	validationMD := map[string]json.RawMessage{"token": json.RawMessage(`"native-api"`), "scraper_type": json.RawMessage(`"skip"`)}
	for key, value := range md {
		if monitorRuntimeFields[key] || key == "_monitor_config_fingerprint" {
			validationMD[key] = value
		}
	}
	body, err := json.Marshal(validationMD)
	if err != nil {
		return fail()
	}
	validation["metadata"] = string(body)
	if NativeBrowserProfile(profile) || profile == apiSnifferBrowserProfile || profile == inlineRenderedMonitorProfile || RSSRenderedProfile(profile) || profile == dayforceMonitorProfile || profile == domRenderedMonitorProfile || profile == domRenderedRowsProfile || profile == "nextdata.rendered-items/v1" || profile == "nextdata.rendered-urls/v1" {
		validation["monitor_needs_browser"] = "0"
	}
	p, err := InspectGreenhouseMonitor(boardID, validation)
	if err != nil {
		return fail()
	}
	stable, err := stableGreenhouseConfig(config, md)
	if SecondaryProvider(provider) || provider == "mokahr" || provider == "almacareer" || provider == "eightfold" || provider == "personio" || provider == "rss" || provider == "inline" || provider == "beisen" || provider == "sitemap" || provider == "join" || provider == "dom" || provider == "api_sniffer" || provider == "oracle_hcm" || provider == "icims" || provider == "breezy" || provider == "jazzhr" || provider == "gupy" || provider == "phenom" || provider == "jobylon" || provider == "nextdata" || profile == "smartrecruiters.canonical-items/v1" {
		// PostgreSQL jsonb and Redis may order nested filter/detail keys
		// differently. Reuse the existing semantic configuration binding.
		stable, err = stableJSONLDConfig(config, md)
	}
	if err != nil {
		return fail()
	}
	body, err = json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return fail()
	}
	// Replacement order is semantic even though ordinary JSON object order is
	// normalized. Bind the sequence without changing the stored config shape.
	if DOMMonitorUsesRichRows(profile) {
		options, err := DOMMonitorOptions(config)
		if err != nil || options.RichRows == nil {
			return fail()
		}
		ordered, err := json.Marshal(options.RichRows.Replacements)
		if err != nil {
			return fail()
		}
		body = append(append(body, '\n'), ordered...)
	}
	digest := sha256.Sum256(body)
	p.EffectiveConfigSHA256, p.SnapshotSHA256 = hex.EncodeToString(digest[:]), configDigest(config)
	p.Provider, p.Profile, p.Token, p.Endpoint = provider, profile, token, endpoint
	return p, nil
}

// APIMonitorResourceMatches admits only the existing provider's list pages and
// documented Workable fallback resources, without granting claim/write authority.
func APIMonitorResourceMatches(p GreenhouseMonitorProfile, resource string) bool {
	u, err := url.Parse(resource)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" || u.RawPath != "" || u.Port() != "" {
		return false
	}
	if p.Profile == "smartrecruiters.api-urls/v1" || p.Profile == "smartrecruiters.canonical-items/v1" {
		base := smartrecruiters.ListURL(p.Token)
		if p.Profile == "smartrecruiters.canonical-items/v1" && u.RawQuery == "" && strings.HasPrefix(resource, base+"/") {
			return smartrecruiters.PublicationResourceMatches(p.Token, resource)
		}
		q, queryErr := url.ParseQuery(u.RawQuery)
		if queryErr != nil {
			return false
		}
		u.RawQuery = ""
		offset, err := strconv.Atoi(q.Get("offset"))
		return u.String() == base && len(q) == 2 && len(q["limit"]) == 1 && q.Get("limit") == "100" && len(q["offset"]) == 1 && err == nil && offset >= 0 && offset <= 50000 && offset%100 == 0 && strconv.Itoa(offset) == q.Get("offset")
	}
	if p.Profile != "workable.api-urls/v1" && p.Profile != "workable.proxy-api-urls/v1" || u.RawQuery != "" {
		return false
	}
	if u.Host == "www.workable.com" {
		return u.Path == "/api/accounts/"+p.Token
	}
	if u.Host != "apply.workable.com" {
		return false
	}
	return u.Path == "/api/v3/accounts/"+p.Token+"/jobs" || u.Path == "/"+p.Token+"/llms.txt" || u.Path == "/"+p.Token+"/jobs.md"
}
