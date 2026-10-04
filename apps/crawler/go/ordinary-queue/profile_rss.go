package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
)

// Admit only the existing direct Teamtailor rich/skip preset. Other RSS
// variants and detail assignments retain their current owner.
func inspectTeamtailorRich(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	var preset, feed string
	if json.Unmarshal(md["preset"], &preset) != nil || preset != "teamtailor" {
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
		feed = board.Scheme + "://" + board.Host + "/jobs.rss"
	}
	u, err := url.Parse(feed)
	if err != nil || len(feed) > 8192 || u.Scheme != "https" || u.Hostname() == "" || u.Host != u.Hostname() || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || !strings.HasSuffix(u.Path, "/jobs.rss") || strings.ContainsAny(feed, "\x00\r\n") {
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
	profile.Provider, profile.Profile, profile.Endpoint = "rss", "rss.teamtailor-skip/v1", feed
	profile.EffectiveConfigSHA256, profile.SnapshotSHA256 = hex.EncodeToString(digest[:]), configDigest(config)
	return profile, nil
}
