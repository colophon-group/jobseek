package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var rssCategoryQuery = regexp.MustCompile(`^catid=[1-9][0-9]{0,15}$`)

// Admit the existing direct Teamtailor and SuccessFactors feed/skip contracts.
// Other RSS variants and detail assignments retain their current owner.
func inspectRSSRich(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	var preset, feed, variant string
	if json.Unmarshal(md["preset"], &preset) != nil || preset != "teamtailor" && preset != "successfactors" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["variant"]; ok && json.Unmarshal(raw, &variant) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if preset == "teamtailor" && variant != "" || preset == "successfactors" && variant != "" && variant != "feed" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["feed_url"]; ok && json.Unmarshal(raw, &feed) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if feed == "" {
		board, err := url.Parse(config["board_url"])
		if err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		path := "/jobs.rss"
		if preset == "successfactors" {
			path = "/googlefeed.xml"
		}
		feed = board.Scheme + "://" + board.Host + path
	}
	u, err := url.Parse(feed)
	if err != nil || len(feed) > 8192 || u.Scheme != "https" || u.Hostname() == "" || u.Host != u.Hostname() || u.User != nil || u.Opaque != "" || u.Fragment != "" || strings.ContainsAny(feed, "\x00\r\n") {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	validFeed := u.RawQuery == "" && strings.HasSuffix(u.Path, "/jobs.rss")
	if preset == "successfactors" {
		validFeed = u.RawQuery == "" && strings.EqualFold(strings.TrimRight(u.Path, "/"), "/googlefeed.xml") || u.Path == "/services/rss/category/" && rssCategoryQuery.MatchString(u.RawQuery)
	}
	if !validFeed {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
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
	profile.Provider, profile.Profile, profile.Endpoint = "rss", "rss."+preset+"-skip/v1", feed
	profile.EffectiveConfigSHA256, profile.SnapshotSHA256 = hex.EncodeToString(digest[:]), configDigest(config)
	return profile, nil
}
