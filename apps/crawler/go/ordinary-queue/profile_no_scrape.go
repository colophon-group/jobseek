package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"
)

const noScrapeDetailProfile = "skip.unscheduled-detail/v1"
const browserNoScrapeDetailProfile = "skip.browser-unscheduled-detail/v1"

func NoScrapeDetailProfile(profile string) bool {
	return profile == noScrapeDetailProfile || profile == browserNoScrapeDetailProfile
}

// Bind only the explicit skip case whose original runtime classifier and SQL
// clear predicate both agree. An enrich key, including an empty/null value,
// retains its existing consumer. This never grants origin-fetch authority.
func InspectNoScrapeDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["crawler_type"] == "" {
		return fail()
	}
	if worker != Simple && worker != Browser || worker == Simple && config["scraper_needs_browser"] != "0" || worker == Browser && config["scraper_needs_browser"] != "1" {
		return fail()
	}
	for key, value := range config {
		if !greenhouseBoardFields[key] || len(value) > 1<<20 || !utf8.ValidString(value) || strings.ContainsRune(value, 0) {
			return fail()
		}
	}
	if _, ok := profileInterval(config["check_interval_minutes"], time.Minute); !ok {
		return fail()
	}
	if _, ok := profileInterval(config["scrape_interval_hours"], time.Hour); !ok {
		return fail()
	}
	if len(source) > 8192 || !utf8.ValidString(source) || strings.ContainsRune(source, 0) {
		return fail()
	}
	u, e := url.Parse(source)
	if e != nil || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.Opaque != "" || !validHost(u.Hostname()) {
		return fail()
	}
	md, e := profileMetadataFields(config["metadata"], nil)
	if e != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "skip" {
		return fail()
	}
	if raw, present := md["scraper_config"]; present && string(raw) != "null" {
		sc, e := profileMetadataFields(string(raw), nil)
		if e != nil {
			return fail()
		}
		if _, present := sc["enrich"]; present {
			return fail()
		}
	}
	stable, e := stableJSONLDConfig(config, md)
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
	sum := sha256.Sum256(body)
	profile := noScrapeDetailProfile
	if worker == Browser {
		profile = browserNoScrapeDetailProfile
	}
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: source, Domain: strings.ToLower(u.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(sum[:])}, nil
}

func inspectNoScrapeDetailOwnership(boardID string, c map[string]string) (WorkdayDetailProfile, error) {
	worker := Simple
	if c["scraper_needs_browser"] == "1" {
		worker = Browser
	}
	p, e := InspectNoScrapeDetail(boardID, c, c["board_url"], worker)
	if e == nil {
		p.Domain = "*"
	}
	return p, e
}
