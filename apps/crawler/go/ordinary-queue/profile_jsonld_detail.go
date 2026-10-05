package queue

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

const jsonldDetailProfile = "jsonld.direct-detail/v1"

// A JSON-LD detail board may retain a legacy monitor. Its immutable detail
// context binds the full canonical configuration; the actual posting URL and
// board are resolved again before every pop, request and write.
func InspectJSONLDDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["scraper_needs_browser"] != "0" || config["crawler_type"] == "" {
		return fail()
	}
	for key, value := range config {
		if !greenhouseBoardFields[key] || len(value) > 1<<20 || !utf8.ValidString(value) || strings.ContainsRune(value, 0) {
			return fail()
		}
	}
	if _, ok := profileInterval(config["scrape_interval_hours"], time.Hour); !ok {
		return fail()
	}
	if _, ok := profileInterval(config["check_interval_minutes"], time.Minute); !ok {
		return fail()
	}
	if !utf8.ValidString(source) || len(source) > 8192 || strings.ContainsRune(source, 0) {
		return fail()
	}
	endpoint, err := url.Parse(source)
	if err != nil || (endpoint.Scheme != "https" && endpoint.Scheme != "http") || endpoint.User != nil || endpoint.Opaque != "" || !validHost(endpoint.Hostname()) || (endpoint.Port() != "" && !(endpoint.Scheme == "https" && endpoint.Port() == "443") && !(endpoint.Scheme == "http" && endpoint.Port() == "80")) {
		return fail()
	}
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(metadata["scraper_type"], &scraper) != nil || scraper != "json-ld" {
		return fail()
	}
	options := map[string]any{}
	if raw, ok := metadata["scraper_config"]; ok && string(raw) != "null" {
		fields, err := profileMetadataFields(string(raw), nil)
		if err != nil {
			return fail()
		}
		for k, v := range fields {
			var value any
			if json.Unmarshal(v, &value) != nil {
				return fail()
			}
			options[k] = value
		}
	}
	if jsonld.ValidateConfig(options) != nil {
		return fail()
	}
	var enrichmentFields []string
	// The existing detail processor and SQL writer support a description-only
	// mask: retained monitor titles, locations and employment stay authoritative.
	// Other masks and configured fallback chains still require their own proof.
	for _, key := range []string{"fallback", "enrich"} {
		if value, ok := options[key]; ok && value != nil {
			list, ok := value.([]any)
			if !ok {
				return fail()
			}
			if len(list) > 0 {
				if key != "enrich" || len(list) != 1 || list[0] != "description" {
					return fail()
				}
				enrichmentFields = []string{"description"}
			}
		}
	}
	stable, err := stableJSONLDConfig(config, metadata)
	if err != nil {
		return fail()
	}
	body, err := json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return fail()
	}
	digest := sha256.Sum256(body)
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: source, Domain: strings.ToLower(endpoint.Hostname()), Profile: jsonldDetailProfile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), JSONLDConfig: options, EnrichmentFields: enrichmentFields}, nil
}

func inspectDetailOwnership(boardID string, config map[string]string) (WorkdayDetailProfile, error) {
	if profile, err := InspectRenderedDetail(boardID, config, config["board_url"], Browser); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	if profile, err := InspectDOMDetail(boardID, config, config["board_url"], Simple); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	if profile, err := InspectJSONLDDetail(boardID, config, config["board_url"], Simple); err == nil {
		// JSON-LD owns the canonical board's details across public source hosts.
		// The wildcard never supplies a request domain: each claim resolves it
		// from the actual SQL posting and byte-matching cache snapshot.
		profile.Domain = "*"
		return profile, nil
	}
	if profile, err := inspectAPIDetailOwnership(boardID, config); err == nil {
		return profile, nil
	}
	if profile, err := InspectEmbeddedDetail(boardID, config, config["board_url"], Simple); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	if profile, err := inspectHTTPAPIDetail(boardID, config, config["board_url"], Simple, true); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	return inspectWorkdayDetailOwnership(boardID, config)
}

func inspectDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	if profile, err := InspectRenderedDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if profile, err := InspectDOMDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if profile, err := InspectJSONLDDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if profile, err := InspectAPIDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if profile, err := InspectEmbeddedDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if profile, err := InspectHTTPAPIDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	return InspectWorkdayDetail(boardID, config, source, worker)
}

func detailDomainMatches(binding string, profile WorkdayDetailProfile) bool {
	return binding == profile.Domain || (binding == "*" && independentDetailProfile(profile.Profile))
}

func detailSourceDomain(binding ownershipDetail, source string) (string, error) {
	parsed, err := url.Parse(source)
	if err != nil || parsed.User != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || !validHost(parsed.Hostname()) {
		return "", ErrAuthorityLost
	}
	domain := strings.ToLower(parsed.Hostname())
	if binding.Domain != domain && !(binding.Domain == "*" && independentDetailProfile(binding.Profile)) {
		return "", ErrAuthorityLost
	}
	return domain, nil
}

// SQL jsonb and Redis may serialize nested option keys in different orders.
// Canonicalize JSON-LD's full immutable metadata without changing retained
// Workday or monitor plan bytes; duplicate fields are rejected before this step.
func stableJSONLDConfig(config map[string]string, metadata map[string]json.RawMessage) (map[string]string, error) {
	stable, err := stableGreenhouseConfig(config, metadata)
	if err != nil {
		return nil, err
	}
	var normalized map[string]any
	decoder := json.NewDecoder(bytes.NewBufferString(stable["metadata"]))
	decoder.UseNumber()
	if decoder.Decode(&normalized) != nil {
		return nil, ErrUnsupportedProfile
	}
	body, err := json.Marshal(normalized)
	if err != nil {
		return nil, ErrUnsupportedProfile
	}
	stable["metadata"] = string(body)
	return stable, nil
}
