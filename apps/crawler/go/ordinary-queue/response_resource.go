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
