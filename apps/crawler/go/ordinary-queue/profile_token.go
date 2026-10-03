package queue

import (
	"net/url"
	"regexp"
	"strings"
	"unicode"
)

var greenhouseRegionalHost = regexp.MustCompile(`^job-boards(?:\.[\pL\pN_-]+)?\.greenhouse\.io$`)

// Match the existing Python _token_from_url without fetching the board URL.
// The caller has already checked its HTTPS authority. Raw path components
// remain escaped, as with urllib.parse; only query parameters are decoded.
func inferredGreenhouseToken(raw string, parsed *url.URL) string {
	path := ""
	authority := raw[strings.Index(raw, "://")+3:]
	if n := strings.IndexAny(authority, "/?#"); n >= 0 && authority[n] == '/' {
		path = authority[n:]
		if end := strings.IndexAny(path, "?#"); end >= 0 {
			path = path[:end]
		}
	}
	parts := []string{}
	for _, part := range strings.Split(path, "/") {
		if part != "" {
			parts = append(parts, part)
		}
	}
	firstQuery := func(key string) string {
		for _, pair := range strings.Split(parsed.RawQuery, "&") {
			name, value, _ := strings.Cut(pair, "=")
			name, nameErr := url.QueryUnescape(name)
			value, valueErr := url.QueryUnescape(value)
			// Python parse_qs drops blank values and retains first-value order.
			if nameErr == nil && valueErr == nil && name == key && value != "" {
				return value
			}
		}
		return ""
	}
	token := ""
	host := strings.ToLower(parsed.Hostname())
	switch {
	case host == "boards-api.greenhouse.io":
		if len(parts) >= 3 && parts[0] == "v1" && parts[1] == "boards" {
			token = parts[2]
		}
	case host == "boards.greenhouse.io":
		if len(parts) > 0 && parts[0] == "embed" {
			token = firstQuery("for")
		} else if len(parts) > 0 {
			token = parts[0]
		}
	case greenhouseRegionalHost.MatchString(host):
		if len(parts) > 0 {
			token = parts[0]
		} else {
			token = firstQuery("url_token")
			if token == "" {
				token = firstQuery("for")
			}
		}
	}
	token = strings.TrimFunc(token, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
	switch token {
	case "embed", "v1", "api", "js", "css", "assets":
		return ""
	}
	return token
}
