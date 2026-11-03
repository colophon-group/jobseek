package main

import (
	"net/url"
	"strings"
)

func detailCaptureMatches(source, method string) bool {
	u, err := url.Parse(source)
	if err != nil || u.User != nil || u.Hostname() == "" || u.Scheme != "https" && u.Scheme != "http" || method != "GET" && method != "POST" {
		return false
	}
	for _, pattern := range []string{"google-analytics", "analytics", "dataplane.rum", "doubleclick", "facebook", "hotjar", "sentry", "segment", "amplitude", "mixpanel", "newrelic", "cloudwatch", "boomerang", "demdex.net", "omtrdc.net", "googlesyndication", "googletagmanager", "gtag", "secureprivacy"} {
		if strings.Contains(strings.ToLower(source), pattern) {
			return false
		}
	}
	return true
}
