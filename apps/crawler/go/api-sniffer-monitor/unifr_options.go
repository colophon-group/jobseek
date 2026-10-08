package apisniffer

import (
	_ "embed"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

//go:embed unifr_sources.json
var unifrSourceContracts []byte

const UnifrCentralFR = "https://www.unifr.ch/sp/fr/postes-vacants.html"
const UnifrCentralDE = "https://www.unifr.ch/sp/de/offene-stellen.html"
const UnifrDetailRoot = "https://webapps.unifr.ch/sp/ws/b49e151b85b415a7201e88d3e9ecf54f11d7589e/detail"

type UnifrOptions struct {
	Name, Kind, URL, Suffix, Heading, Selector string
	ExpectedIDs                                []string          `json:"expected_ids"`
	ExcludedCentralIDs                         map[string]string `json:"excluded_central_ids"`
	DeadlineRequired                           []string          `json:"deadline_required"`
	ImmediatelyAvailable                       []string          `json:"immediately_available"`
	PathPrefix                                 string            `json:"path_prefix"`
	RewriteFrom                                string            `json:"rewrite_from"`
	RewriteTo                                  string            `json:"rewrite_to"`
}

var unifrNumericID = regexp.MustCompile(`^[1-9][0-9]{0,9}$`)

func UnifrOptionsFromMetadata(board, raw string) (UnifrOptions, error) {
	m, err := DecodeInlineMetadata(raw)
	if err != nil {
		return UnifrOptions{}, ErrOptions
	}
	name, ok := m["source"].(string)
	if !ok {
		return UnifrOptions{}, ErrOptions
	}
	var sources map[string]UnifrOptions
	if json.Unmarshal(unifrSourceContracts, &sources) != nil {
		return UnifrOptions{}, ErrOptions
	}
	o, ok := sources[name]
	if !ok || board != o.URL {
		return UnifrOptions{}, ErrOptions
	}
	for key := range m {
		switch key {
		case "source", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return UnifrOptions{}, ErrOptions
		}
	}
	o.Name = name
	return o, nil
}

func (o UnifrOptions) ResourceMatches(resource string) bool {
	if resource == o.URL {
		return true
	}
	if o.Kind == "central" || len(o.ExcludedCentralIDs) > 0 {
		if resource == UnifrCentralFR || resource == UnifrCentralDE {
			return true
		}
	}
	if o.Kind != "central" {
		return false
	}
	if !strings.HasPrefix(resource, UnifrDetailRoot+"/") {
		return false
	}
	parts := strings.Split(strings.TrimPrefix(resource, UnifrDetailRoot+"/"), "/")
	return len(parts) == 2 && (parts[0] == "fr" || parts[0] == "de") && unifrNumericID.MatchString(parts[1])
}

func unifrJobURL(source, identifier string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || len(identifier) == 0 || len(identifier) > 128 {
		return "", ErrInventory
	}
	q := u.Query()
	q.Set("_jid", identifier)
	u.RawQuery = q.Encode()
	u.Fragment = ""
	return u.String(), nil
}
