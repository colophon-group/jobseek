package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var rssCategoryQuery = regexp.MustCompile(`^catid=[1-9][0-9]{0,15}$`)

// Native ownership of direct Teamtailor and SuccessFactors feeds preserves
// their configured detail assignment and downstream URL policy.
func inspectRSSRich(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	var preset, feed, variant string
	if json.Unmarshal(md["preset"], &preset) != nil || preset != "teamtailor" && preset != "successfactors" && preset != "generic" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["variant"]; ok && json.Unmarshal(raw, &variant) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if (preset == "teamtailor" || preset == "generic") && variant != "" || preset == "successfactors" && variant != "" && variant != "feed" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["feed_url"]; ok && json.Unmarshal(raw, &feed) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if feed == "" && preset == "generic" {
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
	validFeed := preset == "generic" || u.RawQuery == "" && strings.HasSuffix(u.Path, "/jobs.rss")
	if preset == "successfactors" {
		validFeed = u.RawQuery == "" && strings.EqualFold(strings.TrimRight(u.Path, "/"), "/googlefeed.xml") || u.Path == "/services/rss/category/" && rssCategoryQuery.MatchString(u.RawQuery)
	}
	if !validFeed {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if err := feedRichDetailAssignment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if _, err := FeedMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	name := "rss." + preset + "-skip/v1"
	if feedHasDetailAssignment(md) {
		name = "rss." + preset + "-items/v1"
	}
	profile, err := inspectURLOnlyMonitor(boardID, config, md, "rss", name, "feed", feed)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return profile, nil
}
