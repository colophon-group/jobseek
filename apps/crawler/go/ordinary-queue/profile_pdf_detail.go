package queue

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"
)

const pdfDetailProfile = "pdf.public-detail/v1"

// InspectPDFDetail binds the configured public PDF scraper independently of its monitor.
func InspectPDFDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
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
	if json.Unmarshal(metadata["scraper_type"], &scraper) != nil || scraper != "pdf" {
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
			decoder := json.NewDecoder(bytes.NewReader(v))
			decoder.UseNumber()
			if decoder.Decode(&value) != nil {
				return fail()
			}
			options[k] = value
		}
	}
	options, proxy := httpDetailParsingOptions(options)
	enrich, err := monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true})
	if err != nil || proxy {
		return fail()
	}
	delete(options, "enrich")
	if _, err := api.PDFOptionsFromConfig(options); err != nil {
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
	profile := pdfDetailProfile
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: source, Domain: strings.ToLower(endpoint.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), PDFConfig: options, EnrichmentFields: enrich}, nil
}
