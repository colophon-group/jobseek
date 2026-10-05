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

// These profiles keep the existing URL-only inventory and separately scheduled
// detail contract. SmartRecruiters' opt-in requisition identities remain outside
// this profile until the rich identity writer is integrated.
func inspectAPIMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
	provider := config["crawler_type"]
	var metadata map[string]any
	if json.Unmarshal([]byte(config["metadata"]), &metadata) != nil {
		return fail()
	}
	var token, endpoint string
	if provider == "smartrecruiters" {
		options, err := smartrecruiters.OptionsFromMetadata(config["board_url"], metadata)
		if err != nil || options.Identity != "" || options.Template != nil {
			return fail()
		}
		token, endpoint = options.Token, smartrecruiters.ListURL(options.Token)+"?limit=100&offset=0"
	} else {
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
	return inspectURLOnlyMonitor(boardID, config, md, provider, provider+".api-urls/v1", token, endpoint)
}

// Reuse the existing configuration/transport authority and URL-only lifecycle
// for inventory parsers whose details are scheduled separately.
func inspectURLOnlyMonitor(boardID string, config map[string]string, md map[string]json.RawMessage, provider, profile, token, endpoint string) (GreenhouseMonitorProfile, error) {
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
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
	if profile == domRenderedMonitorProfile {
		validation["monitor_needs_browser"] = "0"
	}
	p, err := InspectGreenhouseMonitor(boardID, validation)
	if err != nil {
		return fail()
	}
	stable, err := stableGreenhouseConfig(config, md)
	if provider == "sitemap" || provider == "join" || provider == "dom" || provider == "api_sniffer" || provider == "oracle_hcm" || provider == "icims" || provider == "breezy" || provider == "jazzhr" || provider == "gupy" || provider == "phenom" || provider == "jobylon" {
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
	if p.Profile == "smartrecruiters.api-urls/v1" {
		base := smartrecruiters.ListURL(p.Token)
		q, queryErr := url.ParseQuery(u.RawQuery)
		if queryErr != nil {
			return false
		}
		u.RawQuery = ""
		offset, err := strconv.Atoi(q.Get("offset"))
		return u.String() == base && len(q) == 2 && len(q["limit"]) == 1 && q.Get("limit") == "100" && len(q["offset"]) == 1 && err == nil && offset >= 0 && offset <= 50000 && offset%100 == 0 && strconv.Itoa(offset) == q.Get("offset")
	}
	if p.Profile != "workable.api-urls/v1" || u.RawQuery != "" {
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
