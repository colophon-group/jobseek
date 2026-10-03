package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var tenantSlug = regexp.MustCompile(`^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$`)
var recruiteeTenantHost = regexp.MustCompile(`^([a-z0-9-]+)\.recruitee\.com$`)
var pinpointTenantHost = regexp.MustCompile(`^([a-z0-9-]+)\.pinpointhq\.com$`)

// Match the Python monitor's slug/api_base precedence. URL aliases are retained
// for configuration binding but never gain token-selection semantics.
func richTenantEndpoint(config map[string]string, md map[string]json.RawMessage) (string, error) {
	read := func(key string) (string, error) {
		var value string
		if raw, exists := md[key]; exists && json.Unmarshal(raw, &value) != nil {
			return "", ErrUnsupportedProfile
		}
		return value, nil
	}
	if config["crawler_type"] == "recruitee" {
		base, err := read("api_base")
		if err != nil {
			return "", err
		}
		if base != "" {
			return richTenantURL(base + "/api/offers")
		}
	}
	slug, err := read("slug")
	if err != nil {
		return "", err
	}
	board, err := url.Parse(config["board_url"])
	if err != nil || board.Hostname() == "" {
		return "", ErrUnsupportedProfile
	}
	if slug == "" {
		pattern := recruiteeTenantHost
		if config["crawler_type"] == "pinpoint" {
			pattern = pinpointTenantHost
		}
		if match := pattern.FindStringSubmatch(strings.ToLower(board.Hostname())); match != nil {
			slug = match[1]
			switch slug {
			case "www", "api", "app", "docs", "help", "support", "status":
				slug = ""
			}
		}
	}
	if config["crawler_type"] == "pinpoint" {
		if !tenantSlug.MatchString(slug) {
			return "", ErrUnsupportedProfile
		}
		return richTenantURL("https://" + slug + ".pinpointhq.com/postings.json")
	}
	base := board.Scheme + "://" + board.Hostname()
	if slug != "" {
		if !tenantSlug.MatchString(slug) {
			return "", ErrUnsupportedProfile
		}
		base = "https://" + slug + ".recruitee.com"
	}
	return richTenantURL(base + "/api/offers")
}

func richTenantURL(endpoint string) (string, error) {
	if len(endpoint) > 4096 {
		return "", ErrUnsupportedProfile
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(endpoint, "\x00\r\n") {
		return "", ErrUnsupportedProfile
	}
	u.Host = strings.ToLower(u.Host)
	return u.String(), nil
}
