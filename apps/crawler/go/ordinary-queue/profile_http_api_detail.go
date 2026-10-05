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

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const httpAPIDetailProfile = "api_sniffer.http-detail/v1"

// The canonical board still owns each actual detail URL. Wildcard admission
// never supplies a request endpoint and does not bypass the posting/cache fence.
func inspectHTTPAPIDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["scraper_needs_browser"] != "0" || config["crawler_type"] == "" {
		return fail()
	}
	for k, v := range config {
		if !greenhouseBoardFields[k] || len(v) > 1<<20 || !utf8.ValidString(v) || strings.ContainsRune(v, 0) {
			return fail()
		}
	}
	if _, ok := profileInterval(config["scrape_interval_hours"], time.Hour); !ok {
		return fail()
	}
	if _, ok := profileInterval(config["check_interval_minutes"], time.Minute); !ok {
		return fail()
	}
	if len(source) > 8192 || !utf8.ValidString(source) || strings.ContainsRune(source, 0) {
		return fail()
	}
	u, err := url.Parse(source)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.Opaque != "" || !validHost(u.Hostname()) || (u.Port() != "" && !(u.Scheme == "https" && u.Port() == "443") && !(u.Scheme == "http" && u.Port() == "80")) {
		return fail()
	}
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "api_sniffer" {
		return fail()
	}
	options := map[string]any{}
	if raw, ok := md["scraper_config"]; ok && string(raw) != "null" {
		fields, err := profileMetadataFields(string(raw), nil)
		if err != nil {
			return fail()
		}
		for k, v := range fields {
			// Reject ambiguous auth before any credential response is fetched.
			if k == "auth_request" && string(v) != "null" {
				if _, err := profileMetadataFields(string(v), nil); err != nil {
					return fail()
				}
			}
			var decoded any
			d := json.NewDecoder(bytes.NewReader(v))
			d.UseNumber()
			if d.Decode(&decoded) != nil {
				return fail()
			}
			options[k] = decoded
		}
	}
	if apisniffer.ValidateHTTPDetail(options) != nil {
		return fail()
	}
	endpoint := ""
	if !ownership {
		request, err := apisniffer.HTTPDetailOptionsForSource(options, source)
		if err != nil {
			return fail()
		}
		endpoint = request.Request.URL
	}
	var enrichment []string
	if selected, ok := options["enrich"].([]any); ok {
		for _, v := range selected {
			enrichment = append(enrichment, v.(string))
		}
	}
	stable, err := stableJSONLDConfig(config, md)
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
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: endpoint, Domain: strings.ToLower(u.Hostname()), Profile: httpAPIDetailProfile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), HTTPAPIConfig: options, EnrichmentFields: enrichment}, nil
}

func InspectHTTPAPIDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	return inspectHTTPAPIDetail(boardID, config, source, worker, false)
}
