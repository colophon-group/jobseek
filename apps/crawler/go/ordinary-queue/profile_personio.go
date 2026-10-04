package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var personioHost = regexp.MustCompile(`^([a-z0-9_-]+)\.jobs\.personio\.([a-z0-9_]+)$`)
var personioLanguage = regexp.MustCompile(`^[a-z]{2}$`)

func inspectPersonioRich(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	slug, region, language := "", "de", "en"
	board, err := url.Parse(config["board_url"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if match := personioHost.FindStringSubmatch(strings.ToLower(board.Hostname())); match != nil {
		slug, region = match[1], match[2]
		if map[string]bool{"www": true, "api": true, "app": true, "docs": true, "help": true, "support": true, "status": true}[slug] {
			slug = ""
		}
	}
	if raw, ok := md["slug"]; ok {
		var explicit string
		if json.Unmarshal(raw, &explicit) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if explicit != "" {
			slug = explicit
		}
	}
	if raw, ok := md["language"]; ok && (strings.TrimSpace(string(raw)) == "null" || json.Unmarshal(raw, &language) != nil) {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	backfill := []string{"de"}
	if raw, ok := md["backfill_languages"]; ok && strings.TrimSpace(string(raw)) != "null" {
		if json.Unmarshal(raw, &backfill) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	if !greenhouseToken.MatchString(slug) || !personioLanguage.MatchString(language) || region != "de" && region != "com" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	seen := map[string]bool{}
	for _, alternate := range backfill {
		if !personioLanguage.MatchString(alternate) || seen[alternate] {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		seen[alternate] = true
	}
	validation := cloneConfig(config)
	validation["crawler_type"] = "greenhouse"
	common := map[string]json.RawMessage{}
	for key, value := range md {
		if greenhouseMetadataFields[key] {
			common[key] = value
		}
	}
	common["token"] = json.RawMessage(`"provider-token"`)
	body, err := json.Marshal(common)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	validation["metadata"] = string(body)
	profile, err := InspectGreenhouseMonitor(boardID, validation)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	stable, err := stableGreenhouseConfig(config, md)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	body, err = json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	digest := sha256.Sum256(body)
	profile.Provider, profile.Profile = "personio", "personio.xml-skip/v1"
	profile.Token, profile.Region, profile.Language, profile.BackfillLanguages = slug, region, language, backfill
	profile.Endpoint = "https://" + slug + ".jobs.personio." + region + "/xml?language=" + language
	profile.EffectiveConfigSHA256, profile.SnapshotSHA256 = hex.EncodeToString(digest[:]), configDigest(config)
	return profile, nil
}
