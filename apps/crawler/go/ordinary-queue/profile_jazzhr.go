package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var jazzHRTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var jazzHRPath = regexp.MustCompile(`(?i)^(?:/apply(?:/jobs(?:/details/[A-Za-z0-9_-]+)?)?)?/?$`)

func normalizeJazzHRTenant(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if !jazzHRTenant.MatchString(value) || map[string]bool{"app": true, "developers": true, "login": true, "portal": true, "support": true, "www": true}[value] {
		return ""
	}
	return value
}

func inspectJazzHRMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	direct, configured := "", ""
	u, err := url.Parse(config["board_url"])
	if err == nil && u.Scheme == "https" && u.User == nil && (u.Port() == "" || u.Port() == "443") && strings.Count(u.Hostname(), ".") == 2 && strings.HasSuffix(strings.ToLower(u.Hostname()), ".applytojob.com") && jazzHRPath.MatchString(u.Path) {
		direct = normalizeJazzHRTenant(strings.TrimSuffix(strings.ToLower(u.Hostname()), ".applytojob.com"))
	}
	if raw, exists := md["tenant"]; exists {
		var text string
		if json.Unmarshal(raw, &text) != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		configured = normalizeJazzHRTenant(text)
		if configured == "" {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	if direct != "" && configured != "" && direct != configured {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	tenant := direct
	if tenant == "" {
		tenant = configured
	}
	if tenant == "" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "jazzhr", "jazzhr.listing-urls/v1", tenant, "https://"+tenant+".applytojob.com/apply/jobs")
}
