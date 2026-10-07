package queue

import (
	"net/url"
	"strings"
	"unicode"
	"unicode/utf8"
)

// A resource URL is evidence only. The native fetch must bind its initial
// token endpoint and completed final response; the installed claim still
// supplies every database/queue permission. This performs no URL retrieval.
func validGreenhouseResponseResource(resource string) bool {
	if resource == "" || len(resource) > 8192 || !utf8.ValidString(resource) || strings.ContainsFunc(resource, unicode.IsControl) {
		return false
	}
	parsed, err := url.Parse(resource)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") && parsed.Hostname() != "" && parsed.User == nil && parsed.Opaque == "" && parsed.String() == resource
}

func initialMonitorResourceMatches(profile GreenhouseMonitorProfile, resource string) bool {
	if profile.Profile == "rss.hr_manager-skip/v1" || profile.Profile == "rss.hr_manager-items/v1" {
		return resource == profile.Endpoint || resource == profile.Token
	}
	if profile.Provider == "phenom" {
		return PhenomMonitorResourceMatches(profile, resource)
	}
	if profile.Provider == "sitemap" {
		return SitemapMonitorResourceMatches(profile, resource)
	}
	if profile.Provider == "join" {
		return JoinMonitorResourceMatches(profile, resource)
	}
	if profile.Provider == "smartrecruiters" || profile.Provider == "workable" {
		return APIMonitorResourceMatches(profile, resource)
	}
	if profile.Provider != "workday" {
		return resource == profile.Endpoint
	}
	u, err := url.Parse(resource)
	if err != nil || !validGreenhouseResponseResource(resource) || u.Scheme != "https" || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" {
		return false
	}
	endpoint, err := url.Parse(profile.Endpoint)
	if err != nil || u.Host != endpoint.Host {
		return false
	}
	if u.Path == "/robots.txt" {
		return true
	}
	p := strings.Split(strings.TrimPrefix(u.Path, "/"), "/")
	base := strings.Split(strings.TrimPrefix(endpoint.Path, "/"), "/")
	return len(p) == 5 && len(base) == 5 && p[0] == "wday" && p[1] == "cxs" && p[2] == base[2] && greenhouseToken.MatchString(p[3]) && p[4] == "jobs"
}
