package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
)

const domRenderedDetailProfile = "dom.rendered-detail/v1"
const jsonldRenderedDetailProfile = "jsonld.rendered-detail/v1"

func detailWorker(profile string) WorkerType {
	if profile == domRenderedDetailProfile || profile == jsonldRenderedDetailProfile {
		return Browser
	}
	return Simple
}

// InspectRenderedDetail admits the existing browser queue's configured DOM or
// JSON-LD document parser. Unsupported browser operations retain their owner.
// Request defaults match shared.browser.navigate; the full original canonical
// configuration remains the ownership binding.
func InspectRenderedDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Browser || config["scraper_needs_browser"] != "1" {
		return fail()
	}
	parsedSource, err := url.Parse(source)
	if err != nil || parsedSource.Fragment != "" {
		return fail()
	}
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(metadata["scraper_type"], &scraper) != nil || (scraper != "dom" && scraper != "json-ld") {
		return fail()
	}
	if scraper == "json-ld" {
		parsed, err := url.Parse(source)
		if err != nil || regexp.MustCompile(`(?i)(^|[.])icims[.]com$`).MatchString(parsed.Hostname()) {
			return fail()
		}
	}
	options := map[string]any{}
	fields, err := profileMetadataFields(string(metadata["scraper_config"]), nil)
	if err != nil {
		return fail()
	}
	for key, raw := range fields {
		var value any
		if json.Unmarshal(raw, &value) != nil {
			return fail()
		}
		options[key] = value
	}
	if options["render"] != true {
		return fail()
	}
	if backend, present := options["browser_backend"]; present && backend != "lightpanda" {
		return fail()
	}
	for _, key := range []string{"actions", "request_headers", "enrich", "fallback"} {
		value := options[key]
		if value == nil {
			continue
		}
		if list, ok := value.([]any); ok && len(list) == 0 {
			continue
		}
		if object, ok := value.(map[string]any); ok && len(object) == 0 && key == "request_headers" {
			continue
		}
		return fail()
	}
	for _, key := range []string{"proxy", "skip_ssl", "same_origin_redirects"} {
		if value := options[key]; value != nil && value != false {
			return fail()
		}
	}
	for _, key := range []string{"channel", "stealth", "headless", "persistent_context", "user_agent", "resource_policy", "block_resource_types", "transport_attempts", "retry_statuses", "fetch_url_transform", "document_fallback", "linked_description", "encoding", "description_selector"} {
		if _, present := options[key]; present {
			return fail()
		}
	}
	if raw, present := options["timeout"]; present {
		n, ok := raw.(float64)
		if !ok || n < 1 || n > 120000 || n != float64(int64(n)) {
			return fail()
		}
	}
	validWait := func(raw any) bool {
		text, ok := raw.(string)
		return ok && (text == "commit" || text == "domcontentloaded" || text == "load" || text == "networkidle")
	}
	if raw, present := options["wait"]; present && !validWait(raw) {
		return fail()
	}
	if raw, present := options["wait_fallback"]; present && raw != nil && !validWait(raw) {
		return fail()
	}
	if raw, present := options["routing_revision"]; present {
		text, ok := raw.(string)
		if !ok || !regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`).MatchString(text) {
			return fail()
		}
	}
	// Reuse the configured static parser admission after removing only the
	// already-validated navigation controls. This clone is never published.
	parser := make(map[string]any, len(options))
	for key, value := range options {
		parser[key] = value
	}
	for _, key := range []string{"browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers"} {
		delete(parser, key)
	}
	parser["render"] = false
	parserBody, err := json.Marshal(parser)
	if err != nil {
		return fail()
	}
	copyMetadata := make(map[string]json.RawMessage, len(metadata))
	for key, value := range metadata {
		copyMetadata[key] = value
	}
	copyMetadata["scraper_config"] = parserBody
	metadataBody, err := json.Marshal(copyMetadata)
	if err != nil {
		return fail()
	}
	copyConfig := cloneConfig(config)
	copyConfig["scraper_needs_browser"], copyConfig["metadata"] = "0", string(metadataBody)
	var profile WorkdayDetailProfile
	if scraper == "dom" {
		profile, err = InspectDOMDetail(boardID, copyConfig, source, Simple)
		profile.Profile, profile.DOMConfig = domRenderedDetailProfile, options
	} else {
		profile, err = InspectJSONLDDetail(boardID, copyConfig, source, Simple)
		profile.Profile, profile.JSONLDConfig = jsonldRenderedDetailProfile, options
	}
	if err != nil {
		return fail()
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
	profile.EffectiveBoardSHA256 = hex.EncodeToString(digest[:])
	return profile, nil
}
