package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var breezyHost = regexp.MustCompile(`^[\pL\pN_-]+\.breezy\.hr$`)

func inspectBreezyMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	var portal string
	if raw := md["portal_url"]; raw != nil && string(raw) != "null" {
		var text string
		if json.Unmarshal(raw, &text) == nil {
			u, err := url.Parse(text)
			if err == nil && u.Hostname() != "" && u.Scheme == "https" && u.User == nil && u.Port() == "" {
				portal = "https://" + strings.ToLower(u.Hostname())
			} else {
				return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
			}
		}
	}
	if portal == "" {
		u, err := url.Parse(config["board_url"])
		if err == nil && u.Scheme == "https" && u.User == nil && u.Port() == "" {
			host := strings.ToLower(strings.Trim(u.Hostname(), "."))
			if breezyHost.MatchString(host) && !map[string]bool{"www": true, "api": true, "app": true, "developer": true, "marketing": true, "assets-cdn": true, "attachments-cdn": true, "gallery-cdn": true}[strings.TrimSuffix(host, ".breezy.hr")] {
				portal = "https://" + host
			}
		}
	}
	if portal == "" {
		var slug string
		if json.Unmarshal(md["slug"], &slug) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		slug = strings.TrimSpace(slug)
		if !regexp.MustCompile(`^[\pL\pN_-]+$`).MatchString(slug) {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		portal = "https://" + slug + ".breezy.hr"
	}
	return inspectURLOnlyMonitor(boardID, config, md, "breezy", "breezy.api-urls/v1", portal, portal+"/json")
}
