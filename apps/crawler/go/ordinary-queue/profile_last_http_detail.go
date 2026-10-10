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

func LastHTTPDetailProfile(profile string) bool {
	return profile == "infor.session-detail/v1" || profile == "peoplesoft.session-detail/v1"
}

func inspectLastHTTPDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
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
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var provider string
	if json.Unmarshal(md["scraper_type"], &provider) != nil || provider != "infor" && provider != "peoplesoft" {
		return fail()
	}
	raw := md["scraper_config"]
	if len(raw) == 0 || string(raw) == "null" {
		raw = json.RawMessage(`{}`)
	}
	detailMD, err := profileMetadataFields(string(raw), map[string]bool{"enrich": true})
	if err != nil {
		return fail()
	}
	validation := cloneConfig(config)
	validation["crawler_type"] = provider
	enrich, err := lastHTTPMonitorEnrichment(validation)
	if err != nil {
		return fail()
	}
	if ownership {
		o, err := api.LastHTTPOptionsFromMetadata(provider, config["board_url"], "{}")
		if err != nil {
			return fail()
		}
		if provider == "infor" {
			source = o.InforJobURL("1", "1")
		} else {
			source = o.PeopleSoftJobURL("1")
		}
	}
	o, err := api.LastHTTPDetailOptionsFromConfig(provider, config["board_url"], source, string(raw))
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
	options := map[string]any{"board_url": config["board_url"]}
	for key, value := range detailMD {
		var v any
		if json.Unmarshal(value, &v) != nil {
			return fail()
		}
		options[key] = v
	}
	u, _ := url.Parse(source)
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: o.Endpoint, Domain: strings.ToLower(u.Hostname()), Profile: o.Profile(), EffectiveBoardSHA256: hex.EncodeToString(digest[:]), HTTPAPIConfig: options, EnrichmentFields: enrich}
	if ownership {
		p.SourceURL = config["board_url"]
		p.Domain = "*"
	}
	return p, nil
}
