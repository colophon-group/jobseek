package main

import (
	_ "embed"
	"encoding/json"
	"reflect"
	"regexp"
)

// Both configurations are committed with the release. Restoring the old CSV
// selects the reverse lane without replacing scores or cached lifecycle data.
type browserHTTPRouteConfig struct {
	Slug          string         `json:"slug"`
	BoardURL      string         `json:"board_url"`
	Domain        string         `json:"domain"`
	ScraperType   string         `json:"scraper_type"`
	SourcePattern string         `json:"source_pattern"`
	Forward       map[string]any `json:"forward_config"`
	Reverse       map[string]any `json:"reverse_config"`
}

//go:embed provider-cutover/browser-http-routes.json
var browserHTTPRouteContracts []byte

func browserHTTPRouteTargets() []workerRouteTarget {
	var configs []browserHTTPRouteConfig
	if json.Unmarshal(browserHTTPRouteContracts, &configs) != nil || len(configs) != 6 {
		panic("invalid committed browser HTTP route contracts")
	}
	targets := make([]workerRouteTarget, 0, len(configs))
	seen := map[string]bool{}
	for _, config := range configs {
		if config.Slug == "" || seen[config.Slug] || config.BoardURL == "" || config.Domain == "" ||
			(config.ScraperType != "dom" && config.ScraperType != "json-ld") ||
			config.Forward["render"] != false || config.Reverse["render"] != true {
			panic("invalid committed browser HTTP route identity")
		}
		seen[config.Slug] = true
		targets = append(targets, workerRouteTarget{
			Slug: config.Slug, BoardURL: config.BoardURL, Domain: config.Domain,
			LegacyType: config.ScraperType, SourcePattern: regexp.MustCompile(config.SourcePattern), StaticConfigs: &config,
		})
	}
	return targets
}

func (c *browserHTTPRouteConfig) matches(kind string, config map[string]any, browser bool) bool {
	want := c.Forward
	if browser {
		want = c.Reverse
	}
	return kind == c.ScraperType && reflect.DeepEqual(config, want)
}
