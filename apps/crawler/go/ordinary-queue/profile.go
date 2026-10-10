package queue

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

var ErrUnsupportedProfile = errors.New("ordinary monitor profile is unsupported")
var greenhouseToken = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

// GreenhouseMonitorProfile is a detached eligibility observation, not claim or
// database authority. A supported cutover must bind it to a durable active plan
// and verify enabled/canonical board state before it can affect a queue pop.
type GreenhouseMonitorProfile struct {
	RSSPagination                               *RSSPagination
	RSSDetailEnrichment                         bool
	BoardID, CompanyID, Domain, Token, Endpoint string
	Provider, Region, Profile                   string
	Language                                    string
	BackfillLanguages                           []string
	CheckInterval, ScrapeInterval               time.Duration
	EffectiveConfigSHA256, SnapshotSHA256       string
}

var greenhouseBoardFields = map[string]bool{
	"board_slug": true, "board_url": true, "crawler_type": true, "company_id": true,
	"metadata": true, "check_interval_minutes": true, "scrape_interval_hours": true,
	"throttle_key": true, "domain": true, "monitor_needs_browser": true,
	"scraper_needs_browser": true, "egress_host": true, "scrape_egress_host": true,
}
var greenhouseMetadataFields = map[string]bool{
	"token": true, "scraper_type": true, "scraper_config": true,
	"board_token":    true,
	"suspect_streak": true, "recent_discovered_counts": true,
	"_monitor_config_fingerprint": true, "_confirmed_drop_candidate": true,
}
var monitorRuntimeFields = map[string]bool{
	"suspect_streak": true, "recent_discovered_counts": true, "_confirmed_drop_candidate": true,
}

// Read the exact effective object: duplicate keys must not be interpreted
// differently by a plan builder, the runtime and the configuration fingerprint.
func profileMetadata(raw string) (map[string]json.RawMessage, error) {
	return profileMetadataFields(raw, greenhouseMetadataFields)
}

func profileMetadataFields(raw string, allowed map[string]bool) (map[string]json.RawMessage, error) {
	if len(raw) == 0 || len(raw) > 1<<20 {
		return nil, ErrUnsupportedProfile
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return nil, ErrUnsupportedProfile
	}
	fields := map[string]json.RawMessage{}
	for decoder.More() {
		key, err := decoder.Token()
		name, ok := key.(string)
		if err != nil || !ok || (allowed != nil && !allowed[name]) || fields[name] != nil {
			return nil, ErrUnsupportedProfile
		}
		var value json.RawMessage
		if decoder.Decode(&value) != nil {
			return nil, ErrUnsupportedProfile
		}
		fields[name] = value
	}
	end, err := decoder.Token()
	if err != nil || end != json.Delim('}') || decoder.Decode(new(any)) != io.EOF {
		return nil, ErrUnsupportedProfile
	}
	return fields, nil
}

func profileInterval(raw string, unit time.Duration) (time.Duration, bool) {
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value < 1 || strconv.FormatInt(value, 10) != raw || value > int64((1<<63-1)/unit) {
		return 0, false
	}
	return time.Duration(value) * unit, true
}

// InspectGreenhouseMonitor validates the strict rich API profile already used
// by the Go Greenhouse compatibility runtime. Explicit tokens take precedence;
// otherwise the existing recognized URL forms supply the token. Filtering,
// enrichment, proxy/transport overrides and unknown fields remain unselected.
// It reads no queue and makes no request to the configured board URL.
func InspectGreenhouseMonitor(boardID string, config map[string]string) (GreenhouseMonitorProfile, error) {
	var result GreenhouseMonitorProfile
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
	if !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["crawler_type"] != "greenhouse" {
		return fail()
	}
	for key, value := range config {
		if !greenhouseBoardFields[key] || len(value) > 1<<20 || !utf8.ValidString(value) {
			return fail()
		}
	}
	if config["monitor_needs_browser"] != "0" || config["scraper_needs_browser"] != "0" || !validPart(config["domain"]) || len(config["domain"]) > 253 || config["domain"] != config["throttle_key"] || len(config["board_url"]) > 8192 {
		return fail()
	}
	boardURL, err := url.Parse(config["board_url"])
	if err != nil || boardURL.Scheme != "https" || boardURL.User != nil || boardURL.Hostname() == "" || boardURL.Host != boardURL.Hostname() || boardURL.Opaque != "" {
		return fail()
	}
	metadata, err := profileMetadata(config["metadata"])
	if err != nil {
		return fail()
	}
	var token, scraper string
	if raw, present := metadata["token"]; present && json.Unmarshal(raw, &token) != nil {
		return fail()
	}
	if token == "" {
		// Python ignores the legacy board_token field; preserve it in the
		// configuration binding, but infer from the board URL instead.
		token = inferredGreenhouseToken(config["board_url"], boardURL)
	}
	if !greenhouseToken.MatchString(token) || json.Unmarshal(metadata["scraper_type"], &scraper) != nil || scraper != "skip" {
		return fail()
	}
	if raw, present := metadata["scraper_config"]; present && !bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		var options map[string]json.RawMessage
		if json.Unmarshal(raw, &options) != nil || options == nil || len(options) != 0 {
			return fail()
		}
	}
	check, ok := profileInterval(config["check_interval_minutes"], time.Minute)
	if !ok {
		return fail()
	}
	scrape, ok := profileInterval(config["scrape_interval_hours"], time.Hour)
	if !ok {
		return fail()
	}
	// Runtime observation changes must not retire the profile itself. The full
	// snapshot still binds each claim; native persistence must preserve and
	// freshly validate these lifecycle fields before disappearance effects.
	stable, err := stableGreenhouseConfig(config, metadata)
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
	result = GreenhouseMonitorProfile{
		BoardID: boardID, CompanyID: config["company_id"], Domain: config["domain"], Token: token,
		Provider: "greenhouse", Profile: greenhouseOwnershipProfile,
		Endpoint:      "https://boards-api.greenhouse.io/v1/boards/" + token + "/jobs?content=true",
		CheckInterval: check, ScrapeInterval: scrape,
		EffectiveConfigSHA256: hex.EncodeToString(digest[:]), SnapshotSHA256: configDigest(config),
	}
	return result, nil
}

func stableGreenhouseConfig(config map[string]string, metadata map[string]json.RawMessage) (map[string]string, error) {
	stableMetadata := map[string]json.RawMessage{}
	for key, value := range metadata {
		if !monitorRuntimeFields[key] {
			stableMetadata[key] = value
		}
	}
	if config["crawler_type"] == "unisante" {
		if _, err := unisanteMigrationConfig(config); err != nil {
			return nil, ErrUnsupportedProfile
		}
		// This one code-owned receipt is runtime state. The migration flag and
		// every operator field remain part of the immutable configuration.
		if unisanteMigrationRequested(metadata) {
			delete(stableMetadata, "_identity_migration_receipt")
		}
	}
	if config["crawler_type"] == "rss" {
		if err := postfinanceMigrationConfig(config, metadata); err != nil {
			return nil, err
		}
		if postfinanceMigrationRequested(metadata) {
			delete(stableMetadata, "_identity_migration_receipt")
		}
	}
	if config["crawler_type"] == "ukg" {
		board, err := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
		// Legacy discovery persists these aliases after CSV sync. Bind the
		// resolved request target even before that write. Keep conflicting or
		// malformed supplied aliases in the hash, along with all other config.
		values := map[string]string{"host": board.Host, "tenant": board.Tenant,
			"board_id": board.BoardID, "listing_url": board.ListingURL()}
		for key, expected := range values {
			raw, supplied := metadata[key]
			equivalent := !supplied || string(raw) == "null"
			var value string
			if supplied && string(raw) != "null" && json.Unmarshal(raw, &value) == nil {
				switch key {
				case "host":
					equivalent = strings.TrimRight(strings.ToLower(strings.TrimSpace(value)), ".") == expected
				case "tenant":
					equivalent = strings.TrimSpace(value) == expected
				case "board_id":
					equivalent = strings.ToLower(strings.TrimSpace(value)) == expected
				case "listing_url":
					parsed, e := api.UKGBoardFromURL(value)
					equivalent = e == nil && parsed == board
				}
			}
			if equivalent {
				stableMetadata[key], _ = json.Marshal(expected)
			}
		}
	}
	if config["crawler_type"] == "eightfold" {
		watermark, e := stableEightfoldWatermark(metadata["pcsx_watermark"])
		if e != nil {
			return nil, e
		}
		stableMetadata["pcsx_watermark"] = watermark
	}
	stable := cloneConfig(config)
	// These cached egress observations are learned at execution time. The
	// native endpoint remains fixed by the resolved token; publisher/circuit
	// attribution must derive its actual request host rather than trust caches.
	delete(stable, "egress_host")
	delete(stable, "scrape_egress_host")
	body, err := json.Marshal(stableMetadata)
	if err != nil {
		return nil, ErrUnsupportedProfile
	}
	stable["metadata"] = string(body)
	return stable, nil
}
