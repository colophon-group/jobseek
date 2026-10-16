package worker

import (
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/url"
	"strings"
)

type jsonldHTTPDetailRoute struct{ host, pathPrefix string }

// These unchanged browser configurations expose complete JSON-LD over HTTP.
// The original five retain eight positive references and 878 captured routes.
// Ecodrop adds a fresh UTC original/HTTP match and private canonical replay.
// Keep the existing canonical browser queue, ownership
// fingerprint and parser; changed configurations or other origins/paths retain
// their rendered route. No runtime probe, extra origin request or fallback is added.
var qualifiedJSONLDHTTPDetails = map[string]jsonldHTTPDetailRoute{
	// ecodrop-wttj
	"072db00d8c764fd8d8efc35da4021ce9c87cf2a0a9777080af4aafea9155c3d6": {host: "www.welcometothejungle.com", pathPrefix: "/fr/companies/ecodrop/jobs/"},
	// oliver-wyman-marsh-global-early-careers
	"56a2827c5d9a09d4a97e681592754331a97c7a74b97570543f1a66bea46a1c44": {host: "careers.marsh.com", pathPrefix: "/global/en/job/"},
	// oliver-wyman-marsh-eu
	"a604b1e804c8c5f540403eded13a32fdd4d1b06eb256902fa8e904db7d217af2": {host: "careers.marsh.com", pathPrefix: "/eu/en/job/"},
	// oliver-wyman-marsh-eu-early-careers
	"d011f823744641962c9e3c76085ccf3b87d94b7badae1ce1c0184eed2155aeb6": {host: "careers.marsh.com", pathPrefix: "/eu/en/job/"},
	// tencent-careers
	"ee19d95799529563295d3fa36a1cdb479a18f4a7e88d9a720cb129d7e55a8b6b": {host: "tencent.wd1.myworkdayjobs.com", pathPrefix: "/Tencent_Careers/job/"},
	// oliver-wyman-marsh-global
	"fff5c90cca205b35051ef234e7f3e975bf4adad5a51695a53006f65b55c1bb1f": {host: "careers.marsh.com", pathPrefix: "/global/en/job/"},
}

func qualifiedJSONLDHTTPDetail(profile queue.WorkdayDetailProfile) bool {
	if profile.Profile != "jsonld.rendered-detail/v1" || profile.SkipSSL {
		return false
	}
	route, ok := qualifiedJSONLDHTTPDetails[profile.EffectiveBoardSHA256]
	if !ok {
		return false
	}
	u, err := url.Parse(profile.SourceURL)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Hostname() != route.host || (u.Port() != "" && u.Port() != "443") || !strings.HasPrefix(u.EscapedPath(), route.pathPrefix) {
		return false
	}
	for _, segment := range strings.Split(u.Path, "/") {
		if segment == "." || segment == ".." {
			return false
		}
	}
	return true
}

func jsonldHTTPDetailProfile(profile queue.WorkdayDetailProfile) queue.WorkdayDetailProfile {
	// Fetch receives a detached parser/transport view. Claim and SQL authority
	// continue to use the original immutable rendered profile and browser lane.
	options := make(map[string]any, len(profile.JSONLDConfig))
	for key, value := range profile.JSONLDConfig {
		switch key {
		case "render", "wait", "wait_fallback", "timeout", "actions", "browser_backend", "routing_revision":
			continue
		}
		options[key] = value
	}
	profile.Profile, profile.JSONLDConfig = "jsonld.direct-detail/v1", options
	return profile
}
