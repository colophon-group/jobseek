package queue

import (
	"encoding/json"
	"net/url"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	sitemap "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

func SitemapMonitorConfig(config map[string]string) (sitemap.Config, string, string, error) {
	parsed, parseErr := httpMonitorParsingConfig(config)
	if parseErr != nil {
		return sitemap.Config{}, "", "", parseErr
	}
	config = parsed
	var md map[string]json.RawMessage
	fail := func() (sitemap.Config, string, string, error) { return sitemap.Config{}, "", "", ErrUnsupportedProfile }
	if json.Unmarshal([]byte(config["metadata"]), &md) != nil {
		return fail()
	}
	var root string
	if json.Unmarshal(md["sitemap_url"], &root) != nil || root == "" || len(root) > 8192 {
		return fail()
	}
	board, err := url.Parse(config["board_url"])
	u, urlErr := url.Parse(root)
	if err != nil || urlErr != nil || board.Scheme != "https" || u.Scheme != "https" || u.User != nil || u.Host == "" || u.Host != u.Hostname() || u.Fragment != "" || u.Opaque != "" {
		return fail()
	}
	contentAttempts := 1
	if raw, exists := md["xml_attempts"]; exists {
		if json.Unmarshal(raw, &contentAttempts) != nil || contentAttempts < 1 || contentAttempts > 5 {
			return fail()
		}
	}
	for _, key := range []string{"proxy", "render", "skip_ssl"} {
		if raw, exists := md[key]; exists && string(raw) != "false" && string(raw) != "null" && string(raw) != `""` {
			return fail()
		}
	}
	if raw, exists := md["ssl_verify"]; exists && string(raw) != "true" && string(raw) != "null" {
		return fail()
	}
	var include, exclude string
	if raw, exists := md["url_filter"]; exists && string(raw) != "null" {
		if json.Unmarshal(raw, &include) != nil {
			fields, err := profileMetadataFields(string(raw), map[string]bool{"include": true, "exclude": true})
			if err != nil {
				return fail()
			}
			for key, value := range fields {
				if string(value) == "null" {
					continue
				}
				target := &include
				if key == "exclude" {
					target = &exclude
				}
				if json.Unmarshal(value, target) != nil {
					return fail()
				}
			}
		}
	}
	for _, pattern := range []string{include, exclude} {
		if _, err := dom.CompileURLPattern(pattern); err != nil {
			return fail()
		}
	}
	c, err := sitemap.NormalizeConfig(sitemap.Config{SitemapURL: root, MaxURLs: 50_000, MaxIndexChildren: 200, MaxIndexDepth: 8, ChildMaxAttempts: max(3, contentAttempts), RootMaxAttempts: max(3, contentAttempts), RootContentAttempts: contentAttempts})
	if err != nil {
		return fail()
	}
	return c, include, exclude, nil
}

func inspectSitemapMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if _, err := FeedMonitorURLRules(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if raw, ok := md["urls"]; ok {
		var n int
		if json.Unmarshal(raw, &n) != nil || n < 0 || n > 50000 {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	c, _, _, err := SitemapMonitorConfig(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "sitemap", "sitemap.explicit-urls/v1", "sitemap", c.SitemapURL)
}

// SitemapMonitorResourceMatches binds a fetched index child to the configured
// HTTPS origin. It grants no queue or database write authority.
func SitemapMonitorResourceMatches(profile GreenhouseMonitorProfile, resource string) bool {
	if profile.Provider != "sitemap" {
		return false
	}
	root, rootErr := url.Parse(profile.Endpoint)
	child, childErr := url.Parse(resource)
	return rootErr == nil && childErr == nil && validGreenhouseResponseResource(resource) && child.Scheme == "https" && child.Host == root.Host && child.User == nil && child.Opaque == "" && child.Fragment == "" && child.Port() == ""
}
