package queue

import (
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"net/netip"
	"net/url"
	"strings"
)

// A configured alternate listing read changes fetch identity, never the
// official board identity or posting URL transform. Only static single-page
// href inventories qualify; redirects retain their observed resource evidence.
func domMonitorFetchURL(config map[string]string, md map[string]json.RawMessage, c dom.ListingConfig) (string, error) {
	raw, present := md["fetch_url_transform"]
	if !present || string(raw) == "null" {
		return "", nil
	}
	if monitorWorkerProfile(config) == Browser || c.Pagination != nil || c.RichRows != nil || c.ScriptLinks != nil || c.OnclickSelector != "" || c.Proofs != nil || c.RequireJSONLD {
		return "", ErrUnsupportedProfile
	}
	f, err := profileMetadataFields(string(raw), map[string]bool{"find": true, "replace": true})
	if err != nil || len(f) != 2 {
		return "", ErrUnsupportedProfile
	}
	var find, replacement string
	if json.Unmarshal(f["find"], &find) != nil || json.Unmarshal(f["replace"], &replacement) != nil || find == "" || replacement == "" || strings.ContainsRune(find, 0) || strings.ContainsRune(replacement, 0) {
		return "", ErrUnsupportedProfile
	}
	body, _ := json.Marshal(map[string]json.RawMessage{"url_transform": raw})
	rules, err := FeedMonitorURLRules(map[string]string{"metadata": string(body)})
	if err != nil || rules.find == nil || len(rules.find.FindAllStringIndex(config["board_url"], -1)) != 1 {
		return "", ErrUnsupportedProfile
	}
	target := rules.Rewrite(config["board_url"])
	u, err := url.Parse(target)
	if err != nil {
		return "", ErrUnsupportedProfile
	}
	if ip, e := netip.ParseAddr(u.Hostname()); e == nil && (!ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast()) {
		return "", ErrUnsupportedProfile
	}
	if err != nil || u.Hostname() == "localhost" || strings.HasSuffix(u.Hostname(), ".localhost") || len(target) > 8192 || !jsonld.ValidPublicEndpoint(target) {
		return "", ErrUnsupportedProfile
	}
	return target, nil
}

func DOMMonitorPrimaryResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	c, err := DOMMonitorOptions(config)
	if err != nil || p.Provider != "dom" {
		return false
	}
	if c.FetchURL != "" {
		return resource == c.FetchURL
	}
	return resource == p.Endpoint
}
