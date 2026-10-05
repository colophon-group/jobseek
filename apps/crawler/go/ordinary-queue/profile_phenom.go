package queue

import (
	"encoding/json"
	"net/url"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

// Phenom uses only these inventory options. Legacy canvas options and detail
// configuration remain in the immutable binding, without supplying requests.
func PhenomMonitorConfig(config map[string]string) (string, []string, string, error) {
	fail := func() (string, []string, string, error) { return "", nil, "", ErrUnsupportedProfile }
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	board, err := url.Parse(config["board_url"])
	if err != nil || board.Scheme != "https" || board.User != nil || board.Host == "" || board.Host != board.Hostname() {
		return fail()
	}
	// Persisted root is required until native root discovery also owns its
	// metadata publication. Every currently enabled Phenom board supplies it.
	var root string
	if json.Unmarshal(md["sitemap_url"], &root) != nil || root == "" {
		return fail()
	}
	u, err := url.Parse(root)
	if err != nil || u.Scheme != "https" || u.Host != board.Host || u.User != nil || u.Opaque != "" || u.Fragment != "" || len(root) > 8192 {
		return fail()
	}
	// A direct profile cannot replace boards whose current transport is proxy
	// backed. They retain their current owner until native proxy parity is proven.
	for _, key := range []string{"proxy", "render", "skip_ssl"} {
		if raw := md[key]; raw != nil && string(raw) != "false" && string(raw) != "null" && string(raw) != `""` {
			return fail()
		}
	}
	if raw := md["ssl_verify"]; raw != nil && string(raw) != "true" && string(raw) != "null" {
		return fail()
	}
	langs := []string{"en", "en-us"}
	if raw := md["keep_languages"]; raw != nil && string(raw) != "null" {
		var values []string
		if json.Unmarshal(raw, &values) != nil {
			return fail()
		}
		if len(values) > 0 {
			langs = values
		}
	}
	var exclude string
	if raw := md["url_exclude"]; raw != nil && string(raw) != "null" {
		if json.Unmarshal(raw, &exclude) != nil {
			return fail()
		}
	}
	if _, err := dom.CompileURLPattern(exclude); err != nil {
		return fail()
	}
	return root, langs, exclude, nil
}

func inspectPhenomMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	root, _, _, err := PhenomMonitorConfig(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "phenom", "phenom.sitemap-urls/v1", "phenom", root)
}

func PhenomMonitorResourceMatches(p GreenhouseMonitorProfile, resource string) bool {
	root, err := url.Parse(p.Endpoint)
	u, parseErr := url.Parse(resource)
	return p.Provider == "phenom" && err == nil && parseErr == nil && validGreenhouseResponseResource(resource) && u.Scheme == "https" && u.Host == root.Host && u.User == nil && u.Opaque == "" && u.Fragment == "" && u.Port() == ""
}
