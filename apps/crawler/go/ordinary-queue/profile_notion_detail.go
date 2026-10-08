package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const notionDetailProfile = "notion.public-detail/v1"

func inspectNotionDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["scraper_needs_browser"] != "0" || config["crawler_type"] != "notion" {
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
	o, err := api.NotionOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil {
		return fail()
	}
	sub, id, err := api.NotionURL(source)
	if err != nil || sub != o.Subdomain || !ownership && !notionPostingID.MatchString(id) {
		return fail()
	}
	u, err := url.Parse(source)
	if err != nil || u.RawQuery != "" || u.Fragment != "" {
		return fail()
	}
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "notion" {
		return fail()
	}
	options := map[string]any{}
	if raw, exists := md["scraper_config"]; exists && string(raw) != "null" {
		fields, err := profileMetadataFields(string(raw), map[string]bool{"property_map": true, "defaults": true, "enrich": true})
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
	}
	if raw := options["property_map"]; raw != nil {
		values, ok := raw.(map[string]any)
		if !ok || len(values) > 128 {
			return fail()
		}
		for key, value := range values {
			text, ok := value.(string)
			if !ok || len(key) > 256 || len(text) > 256 {
				return fail()
			}
			switch text {
			case "locations", "employment_type", "job_location_type", "metadata.team":
			default:
				return fail()
			}
		}
	}
	if raw := options["defaults"]; raw != nil {
		values, ok := raw.(map[string]any)
		if !ok || len(values) > 16 {
			return fail()
		}
		for key, value := range values {
			switch key {
			case "locations":
				items, ok := value.([]any)
				if !ok || len(items) > 100 {
					return fail()
				}
				for _, item := range items {
					text, ok := item.(string)
					if !ok || strings.TrimSpace(text) == "" || len(text) > 4096 {
						return fail()
					}
				}
			case "employment_type", "job_location_type":
				text, ok := value.(string)
				if !ok || len(text) > 256 {
					return fail()
				}
			default:
				return fail()
			}
		}
	}
	enrich, err := monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true})
	if err != nil {
		return fail()
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
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Domain: o.Subdomain + ".notion.site", Endpoint: "https://" + o.Subdomain + ".notion.site/api/v3/loadPageChunk", Profile: notionDetailProfile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), EmbeddedConfig: options, EnrichmentFields: enrich}, nil
}

var notionPostingID = canonicalUUID

func InspectNotionDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	return inspectNotionDetail(boardID, config, source, worker, false)
}
