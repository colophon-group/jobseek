package queue

import (
	"encoding/json"
	"net/url"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	sitemap "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

func SitemapMonitorConfig(config map[string]string) (sitemap.Config, string, string, error) {
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
	if err != nil || urlErr != nil || board.Scheme != "https" || u.Scheme != "https" || u.User != nil || u.Host == "" || u.Host != u.Hostname() || board.Host != u.Host || u.Fragment != "" || u.Opaque != "" {
		return fail()
	}
	if raw, exists := md["xml_attempts"]; exists && string(raw) != "1" {
		return fail()
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
	c, err := sitemap.NormalizeConfig(sitemap.Config{SitemapURL: root, MaxURLs: 50_000, MaxIndexChildren: 1, RequireURLSet: true})
	if err != nil {
		return fail()
	}
	return c, include, exclude, nil
}

func inspectSitemapMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	c, _, _, err := SitemapMonitorConfig(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "sitemap", "sitemap.explicit-urls/v1", "sitemap", c.SitemapURL)
}
