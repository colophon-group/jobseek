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
	if raw, ok := md["preset"]; ok {
		if json.Unmarshal(raw, &preset) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	} else {
		preset = "generic"
	}
	if preset != "teamtailor" && preset != "successfactors" && preset != "generic" && preset != "governmentjobs" && preset != "zoho_recruit" && preset != "hr_manager" && preset != "wp_job_manager" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["variant"]; ok && json.Unmarshal(raw, &variant) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if preset != "successfactors" && variant != "" || preset == "successfactors" && variant != "" && variant != "feed" && variant != "legacy_xml" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["feed_url"]; ok && json.Unmarshal(raw, &feed) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if preset == "governmentjobs" {
		var e error
		feed, e = governmentJobsFeed(config["board_url"], md, feed)
		if e != nil {
			return GreenhouseMonitorProfile{}, e
		}
	}
	if preset == "hr_manager" {
		o, err := HRManagerOptions(config)
		if err != nil {
			return GreenhouseMonitorProfile{}, err
		}
		feed = o.Feed
	}
	if raw, ok := md["tenant"]; ok {
		var tenant string
		if json.Unmarshal(raw, &tenant) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	if feed == "" && (preset == "generic" || preset == "zoho_recruit") {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if feed == "" {
		board, err := url.Parse(config["board_url"])
		if err != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		path := "/jobs.rss"
		if preset == "wp_job_manager" {
			path = "/?feed=job_feed"
		}
		if preset == "successfactors" {
			path = "/googlefeed.xml"
		}
		feed = board.Scheme + "://" + board.Host + path
	}
	u, err := url.Parse(feed)
	if err != nil || len(feed) > 8192 || u.Scheme != "https" || u.Hostname() == "" || u.Host != u.Hostname() || u.User != nil || u.Opaque != "" || u.Fragment != "" || strings.ContainsAny(feed, "\x00\r\n") {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	validFeed := preset == "wp_job_manager" || preset == "generic" || preset == "zoho_recruit" || preset == "governmentjobs" || preset == "hr_manager" || u.RawQuery == "" && strings.HasSuffix(u.Path, "/jobs.rss")
	if preset == "successfactors" {
		validFeed = u.RawQuery == "" && strings.EqualFold(strings.TrimRight(u.Path, "/"), "/googlefeed.xml") || u.Path == "/services/rss/category/" && rssCategoryQuery.MatchString(u.RawQuery)
		if variant == "legacy_xml" {
			_, company, err := SuccessFactorsLegacyXMLIdentity(feed)
			validFeed = err == nil
			if raw, exists := md["company"]; exists && string(raw) != "null" {
				var configured string
				if json.Unmarshal(raw, &configured) != nil || strings.TrimSpace(configured) != company {
					validFeed = false
				}
			}
		}
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
	profilePreset := preset
	if variant == "legacy_xml" {
		profilePreset = "successfactors-legacy-xml"
	}
	if raw, ok := md["description_mode"]; ok && string(raw) != "null" {
		var mode string
		if preset != "generic" || json.Unmarshal(raw, &mode) != nil || mode != "title_employment_location" {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profilePreset = "generic-summary"
	}
	_, pagination, options, err := RSSOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if options["render"] == true {
		profilePreset = "rendered-" + profilePreset
	}
	name := "rss." + profilePreset + "-skip/v1"
	if feedHasDetailAssignment(md) {
		name = "rss." + profilePreset + "-items/v1"
	}
	token := "feed"
	if preset == "hr_manager" {
		token = config["board_url"]
	}
	profile, err := inspectURLOnlyMonitor(boardID, config, md, "rss", name, token, feed)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	profile.RSSPagination = pagination
	return profile, nil
}

var governmentJobsBoardPath = regexp.MustCompile(`^/careers/([a-z0-9][a-z0-9-]{0,63})/?$`)

func governmentJobsFeed(board string, md map[string]json.RawMessage, configured string) (string, error) {
	u, err := url.Parse(board)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") || (strings.ToLower(u.Hostname()) != "governmentjobs.com" && strings.ToLower(u.Hostname()) != "www.governmentjobs.com") {
		return "", ErrUnsupportedProfile
	}
	match := governmentJobsBoardPath.FindStringSubmatch(strings.ToLower(u.Path))
	if match == nil {
		return "", ErrUnsupportedProfile
	}
	if raw, ok := md["agency"]; ok && string(raw) != "null" {
		var agency string
		if json.Unmarshal(raw, &agency) != nil || strings.ToLower(agency) != match[1] {
			return "", ErrUnsupportedProfile
		}
	}
	expected := "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=" + match[1]
	if raw, ok := md["feed_url"]; ok && string(raw) != "null" && configured == "" {
		return "", ErrUnsupportedProfile
	}
	if configured != "" && configured != expected {
		return "", ErrUnsupportedProfile
	}
	return expected, nil
}

var zohoRSSHost = regexp.MustCompile(`^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.zohorecruit\.(com|ca|eu|in|com\.cn|com\.au|jp|uk|sa)$`)
var zohoRSSID = regexp.MustCompile(`^[0-9]+$`)

func validZohoRSSIdentity(source, identity string) bool {
	if identity == "" {
		return true
	}
	u, err := url.Parse(source)
	if err != nil || len(identity) > 512 {
		return false
	}
	tenant := zohoRSSHost.FindStringSubmatch(strings.TrimRight(strings.ToLower(u.Hostname()), "."))
	if tenant == nil {
		return false
	}
	prefix := "zoho_recruit:" + tenant[1] + "." + tenant[2] + ":"
	return strings.HasPrefix(identity, prefix) && zohoRSSID.MatchString(strings.TrimPrefix(identity, prefix))
}
