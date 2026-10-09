package queue

import (
	"encoding/json"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	"net/url"
	"strconv"
	"strings"
)

// The parser and navigation options remain bound to the original board config.
func RSSOptions(config map[string]string) (string, *RSSPagination, map[string]any, error) {
	md, e := richProfileMetadata(config)
	if e != nil {
		return "", nil, nil, e
	}
	preset := "generic"
	if raw, ok := md["preset"]; ok && json.Unmarshal(raw, &preset) != nil {
		return "", nil, nil, ErrUnsupportedProfile
	}
	pagination, e := RSSPaginationOptions(config["metadata"], preset)
	if e != nil {
		return "", nil, nil, e
	}
	options := map[string]any{}
	for _, key := range []string{"render", "wait", "wait_fallback", "timeout", "browser_backend", "routing_revision", "proxy"} {
		if raw, ok := md[key]; ok {
			var v any
			if json.Unmarshal(raw, &v) != nil {
				return "", nil, nil, ErrUnsupportedProfile
			}
			options[key] = v
		}
	}
	if raw, present := md["render"]; present && string(raw) != "true" && string(raw) != "false" {
		return "", nil, nil, ErrUnsupportedProfile
	}
	if options["render"] == true {
		if preset != "generic" && preset != "wp_job_manager" || monitorWorkerProfile(config) != Browser || validateRenderedNavigation(options) != nil {
			return "", nil, nil, ErrUnsupportedProfile
		}
	} else {
		if config["monitor_needs_browser"] != "0" {
			return "", nil, nil, ErrUnsupportedProfile
		}
		for _, key := range []string{"wait", "wait_fallback", "timeout", "browser_backend", "routing_revision"} {
			if _, ok := options[key]; ok {
				return "", nil, nil, ErrUnsupportedProfile
			}
		}
	}
	return preset, pagination, options, nil
}
func RSSMonitorResourceMatches(profile GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	if profile.Profile == legacySFSessionProfile {
		return LegacySFSessionResourceMatches(profile, config, resource)
	}
	if resource == profile.Endpoint {
		return true
	}
	_, p, _, e := RSSOptions(config)
	if e != nil || p == nil {
		return false
	}
	return rssPageMatches(profile.Endpoint, p, resource)
}
func rssProfilePageMatches(profile GreenhouseMonitorProfile, resource string) bool {
	return rssPageMatches(profile.Endpoint, profile.RSSPagination, resource)
}
func rssPageMatches(endpoint string, p *RSSPagination, resource string) bool {
	if p == nil {
		return resource == endpoint
	}
	values, e := profilePageValues(resource, p.Param)
	if e != nil || len(values) != 1 {
		return false
	}
	page := values[0]
	if page < p.Start || page > 10_000_000 || (page-p.Start)%p.Increment != 0 || p.MaxPages > 0 && (page-p.Start)/p.Increment >= p.MaxPages {
		return false
	}
	expected, e := feed.PageURL(endpoint, page, p.Param)
	return e == nil && expected == resource
}

func RSSRenderedProfile(profile string) bool { return strings.HasPrefix(profile, "rss.rendered-") }

func profilePageValues(resource, param string) ([]int, error) {
	u, e := url.Parse(resource)
	if e != nil {
		return nil, e
	}
	values := u.Query()[param]
	out := []int{}
	for _, raw := range values {
		n, e := strconv.Atoi(raw)
		if e != nil {
			return nil, e
		}
		out = append(out, n)
	}
	return out, nil
}
