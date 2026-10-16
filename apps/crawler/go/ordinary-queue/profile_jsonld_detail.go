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
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

const jsonldDetailProfile = "jsonld.direct-detail/v1"
const jsonldProxyDetailProfile = "jsonld.proxy-detail/v1"

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
	options, proxy := httpDetailParsingOptions(options)
	options, skipSSL, err := httpDetailTLSOptions(options, proxy)
	if err != nil {
		return fail()
	}
	recovery, err := workday.ParseRecoveryConfig(options["workday_fallback"])
	if err != nil || recovery != nil && skipSSL {
		return fail()
	}
	delete(options, "workday_fallback")
	if jsonld.ValidateConfig(options) != nil {
		return fail()
	}
	enrichmentFields, err := monitorEnrichmentFields(config, detailEnrichmentFields)
	if err != nil {
		return fail()
	}
	// Configured fallback stages still require a separate attempt contract.
	for _, key := range []string{"fallback"} {
		if value, ok := options[key]; ok && value != nil {
			list, ok := value.([]any)
			if !ok {
				return fail()
			}
			if len(list) > 0 {
				return fail()
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
	profile := jsonldDetailProfile
	if proxy {
		profile = jsonldProxyDetailProfile
	}
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: source, Domain: strings.ToLower(endpoint.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), JSONLDConfig: options, JSONLDRecovery: recovery, EnrichmentFields: enrichmentFields, SkipSSL: skipSSL}, nil
}

func inspectDetailOwnership(boardID string, config map[string]string) (WorkdayDetailProfile, error) {
	worker := Simple
	if config["scraper_needs_browser"] == "1" {
		worker = Browser
	}
	if p, e := InspectOrderedDetail(boardID, config, config["board_url"], worker); e == nil {
		return orderedDetailOwnership(p), nil
	}
	if p, e := inspectNoScrapeDetailOwnership(boardID, config); e == nil {
		return p, nil
	}
	if p, e := inspectLastHTTPDetail(boardID, config, config["board_url"], Simple, true); e == nil {
		return p, nil
	}
	if p, e := inspectRemainingHTTPDetail(boardID, config, config["board_url"], Simple, true); e == nil {
		return p, nil
	}
	if config["crawler_type"] == "jobstreet" {
		return inspectJobStreetDetail(boardID, config, config["board_url"], Simple, true)
	}
	if profile, err := inspectStaticProviderDetail(boardID, config, config["board_url"], Simple, true); err == nil {
		return profile, nil
	}
	if config["crawler_type"] == "seek" {
		return inspectSeekDetail(boardID, config, config["board_url"], Simple, true)
	}
	if profile, err := InspectPDFDetail(boardID, config, config["board_url"], Simple); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	if config["crawler_type"] == "notion" {
		return inspectNotionDetail(boardID, config, config["board_url"], Simple, true)
	}
	if profile, err := inspectBrowserAPIDetail(boardID, config, config["board_url"], Browser, true); err == nil {
		profile.Domain = "*"
		return profile, nil
	}
	if profile, err := inspectRenderedDetail(boardID, config, config["board_url"], Browser, true); err == nil {
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
	if p, e := InspectOrderedDetail(boardID, config, source, worker); e == nil {
		r := orderedDetailOwnership(p)
		r.Domain = p.Stages[0].Domain
		return r, nil
	}
	if p, e := InspectNoScrapeDetail(boardID, config, source, worker); e == nil {
		return p, nil
	}
	if p, e := inspectLastHTTPDetail(boardID, config, source, worker, false); e == nil {
		return p, nil
	}
	if p, e := inspectRemainingHTTPDetail(boardID, config, source, worker, false); e == nil {
		return p, nil
	}
	if config["crawler_type"] == "jobstreet" {
		return inspectJobStreetDetail(boardID, config, source, worker, false)
	}
	if profile, err := InspectStaticProviderDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if config["crawler_type"] == "seek" {
		return InspectSeekDetail(boardID, config, source, worker)
	}
	if profile, err := InspectPDFDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
	if config["crawler_type"] == "notion" {
		return InspectNotionDetail(boardID, config, source, worker)
	}
	if profile, err := InspectBrowserAPIDetail(boardID, config, source, worker); err == nil {
		return profile, nil
	}
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
