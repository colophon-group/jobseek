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

func RemainingHTTPDetailProfile(profile string) bool {
	return profile == "johdi.api-detail/v1" || profile == "headhunter.api-detail/v1" || profile == "headhunter.proxy-api-detail/v1"
}

func inspectRemainingHTTPDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
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
	metadata, e := profileMetadataFields(config["metadata"], nil)
	if e != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(metadata["scraper_type"], &scraper) != nil || scraper != "johdi" && scraper != "headhunter" {
		return fail()
	}
	raw := metadata["scraper_config"]
	if len(raw) == 0 || string(raw) == "null" {
		raw = json.RawMessage(`{}`)
	}
	allowed := map[string]bool{"company_key": scraper == "johdi", "flow": scraper == "johdi", "locale": scraper == "johdi", "proxy": scraper == "headhunter", "enrich": scraper == "headhunter"}
	detailMD, e := profileMetadataFields(string(raw), allowed)
	if e != nil {
		return fail()
	}
	fields := map[string]bool{}
	if scraper == "headhunter" {
		for _, key := range []string{"description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary"} {
			fields[key] = true
		}
	}
	enrich, e := monitorEnrichmentFields(config, fields)
	if e != nil {
		return fail()
	}
	if ownership {
		if scraper == "johdi" {
			source = strings.TrimRight(config["board_url"], "/") + "#/offer/1/job"
		} else {
			// Wildcard detail ownership binds the canonical board, while every
			// actual claim validates its own HeadHunter site and numeric ID.
			source = "https://hh.ru/vacancy/1"
		}
	}
	o, e := api.RemainingHTTPDetailOptionsFromConfig(scraper, config["board_url"], source, string(raw))
	if e != nil {
		return fail()
	}
	profile := scraper + ".api-detail/v1"
	if o.Proxy {
		profile = "headhunter.proxy-api-detail/v1"
	}
	stable, e := stableJSONLDConfig(config, metadata)
	if e != nil {
		return fail()
	}
	body, e := json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if e != nil {
		return fail()
	}
	digest := sha256.Sum256(body)
	options := map[string]any{}
	for key, value := range detailMD {
		var v any
		if json.Unmarshal(value, &v) != nil {
			return fail()
		}
		options[key] = v
	}
	options["board_url"] = config["board_url"]
	posting, _ := url.Parse(source)
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: o.Endpoint, Domain: strings.ToLower(posting.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), HTTPAPIConfig: options, EnrichmentFields: enrich}
	if ownership {
		p.SourceURL = config["board_url"]
		p.Domain = "*"
	}
	return p, nil
}
