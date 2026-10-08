package queue

import "encoding/json"

var httpMonitorProxyProfiles = map[string]string{
	"umantis.listing-urls/v1":   "umantis.proxy-listing-urls/v1",
	"dom.direct-urls/v1":        "dom.proxy-urls/v1",
	"dom.direct-rows/v1":        "dom.proxy-rows/v1",
	"api_sniffer.http-items/v1": "api_sniffer.proxy-http-items/v1",
	"inline.document-items/v1":  "inline.proxy-document-items/v1",
	"sitemap.explicit-urls/v1":  "sitemap.proxy-explicit-urls/v1",
	"eightfold.pcsx-sitemap/v1": "eightfold.proxy-pcsx-sitemap/v1",
	"phenom.sitemap-urls/v1":    "phenom.proxy-sitemap-urls/v1",
}

// Parsers receive content options, while the immutable profile and process
// client own transport. Only the proven HTTP families may separate proxy=true
// here; browser settings and every other option still pass their strict parser.
// The original configuration remains the claim and ownership hash input.
func httpMonitorParsingConfig(config map[string]string) (map[string]string, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	if string(md["proxy"]) != "true" {
		return config, nil
	}
	switch config["crawler_type"] {
	case "dom", "api_sniffer", "inline", "sitemap", "eightfold", "phenom":
	default:
		return nil, ErrUnsupportedProfile
	}
	if config["monitor_needs_browser"] != "0" {
		return nil, ErrUnsupportedProfile
	}
	md["proxy"] = json.RawMessage("false")
	body, err := json.Marshal(md)
	if err != nil {
		return nil, ErrUnsupportedProfile
	}
	parsed := cloneConfig(config)
	parsed["metadata"] = string(body)
	return parsed, nil
}

// Transport choice is compiled into immutable profile identities; neither a
// generic caller flag nor a runtime selector grants proxy write authority.
func ProfileRequiresProxy(profile string) bool {
	if profile == "jobbank104.proxy-public-items/v1" || profile == "practicematch.proxy-listing-urls/v1" {
		return true
	}
	if profile == "computrabajo.proxy-listing-urls/v1" || profile == "earcu.proxy-feed-items/v1" || profile == "paylocity.proxy-embedded-items/v1" || profile == paylocityProxyDetailProfile || profile == eightfoldProxyDetailProfile || profile == domProxyDetailProfile || profile == jsonldProxyDetailProfile || profile == httpAPIProxyDetailProfile {
		return true
	}
	for _, proxy := range httpMonitorProxyProfiles {
		if profile == proxy {
			return true
		}
	}
	return false
}

func (a *Authority) RequiresProxyHTTP() bool {
	if a == nil || a.ownership == nil {
		return false
	}
	for _, m := range a.ownership.document.Members {
		if ProfileRequiresProxy(m.Profile) {
			return true
		}
	}
	for _, d := range a.ownership.document.Details {
		if ProfileRequiresProxy(d.Profile) {
			return true
		}
	}
	return false
}

// Content parsers remain transport-free. The original canonical metadata still
// binds ownership; only a supported detail inspector may compile proxy authority.
func httpDetailParsingOptions(options map[string]any) (map[string]any, bool) {
	if options["proxy"] != true && options["proxy"] != false {
		return options, false
	}
	parsed := make(map[string]any, len(options))
	for key, value := range options {
		parsed[key] = value
	}
	delete(parsed, "proxy")
	return parsed, options["proxy"] == true
}
