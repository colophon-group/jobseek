package worker

import (
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/cases"
)

type pythonURL struct{ scheme, host, path, params, query, fragment string }

// Preserve urllib identity bytes: net/url resolution percent-escapes Unicode
// paths and rejects invalid percent escapes accepted by the existing crawler.
func pythonJoinURL(base, reference string) (string, error) { return api.PythonJoinURL(base, reference) }

var talSegment = regexp.MustCompile(`/xf-[a-f0-9]+/`)

var volatileSuccessFactors = map[string]bool{"_s.crb": true, "jobAlertController_jobAlertId": true, "jobAlertController_jobAlertName": true, "browserTimeZone": true}

// Classification/canonicalization use Python urllib's decomposition, rather
// than Go's stricter escaped-path parser. Unmodified URLs retain their exact
// bytes, including invalid percent escapes accepted by the Python URL lane.
func parsePythonURL(raw string) (pythonURL, bool) {
	p, ok := api.ParsePythonURL(raw)
	return pythonURL{p.Scheme, p.Host, p.Path, p.Params, p.Query, p.Fragment}, ok
}
func (p pythonURL) String() string {
	return (api.PythonURL{Scheme: p.scheme, Host: p.host, Path: p.path, Params: p.params, Query: p.query, Fragment: p.fragment}).String()
}

func classifyJobURL(raw, board string) string {
	if raw == "" {
		return "invalid"
	}
	p, ok := parsePythonURL(raw)
	if !ok || p.scheme == "" || p.host == "" {
		return "invalid"
	}
	path := strings.TrimRight(p.path, "/")
	if path == "" {
		if api.CVWarehousePostingURL(raw) {
			return ""
		}
		return "bare_host"
	}
	if bp, ok := parsePythonURL(board); ok && strings.EqualFold(bp.host, p.host) && strings.TrimRight(bp.path, "/") == path && p.query == "" {
		if _, err := api.MokahrDetailRouteForSource(raw); err == nil {
			return ""
		}
		if api.JohdiOfferFragment(p.fragment) {
			return ""
		}
		return "board_homepage"
	}
	return ""
}

func queryText(raw string) string {
	raw = strings.ReplaceAll(raw, "+", " ")
	var bytes []byte
	for i := 0; i < len(raw); i++ {
		if raw[i] == '%' && i+2 < len(raw) {
			if decoded, err := url.PathUnescape(raw[i : i+3]); err == nil {
				bytes = append(bytes, decoded[0])
				i += 2
				continue
			}
		}
		bytes = append(bytes, raw[i])
	}
	// urllib's UTF-8 decoding replaces each invalid byte sequence.
	var output strings.Builder
	for len(bytes) > 0 {
		r, size := utf8.DecodeRune(bytes)
		if r == utf8.RuneError && size == 1 {
			// Python replaces a valid-but-incomplete UTF-8 prefix once,
			// rather than replacing each byte of that prefix independently.
			limit := 1
			if bytes[0] >= 0xc2 && bytes[0] <= 0xdf {
				limit = 2
			} else if bytes[0] >= 0xe0 && bytes[0] <= 0xef {
				limit = 3
			} else if bytes[0] >= 0xf0 && bytes[0] <= 0xf4 {
				limit = 4
			}
			for size < limit && size < len(bytes) {
				b := bytes[size]
				if b < 0x80 || b > 0xbf {
					break
				}
				if size == 1 && ((bytes[0] == 0xe0 && b < 0xa0) || (bytes[0] == 0xed && b > 0x9f) || (bytes[0] == 0xf0 && b < 0x90) || (bytes[0] == 0xf4 && b > 0x8f)) {
					break
				}
				size++
			}
		}
		output.WriteRune(r)
		bytes = bytes[size:]
	}
	return output.String()
}

func decimal(raw string) bool {
	if raw == "" {
		return false
	}
	for _, r := range raw {
		if !unicode.Is(unicode.Nd, r) {
			return false
		}
	}
	return true
}

func canonicalJobURL(raw string) string {
	p, ok := parsePythonURL(raw)
	if !ok {
		return raw
	}
	host := strings.ToLower(p.host)
	if host == "careers.overwolf.com" || strings.Contains(host, ".successfactors.") || strings.Contains(host, ".sapsf.") {
		kept := []string{}
		removed := false
		for _, pair := range strings.Split(p.query, "&") {
			if pair == "" {
				continue
			}
			key, value, found := strings.Cut(pair, "=")
			if !found {
				value = ""
			}
			key, value = queryText(key), queryText(value)
			drop := volatileSuccessFactors[key]
			if host == "careers.overwolf.com" {
				folded := cases.Fold().String(key)
				drop = folded == "src" || (folded == "t" && decimal(value))
			}
			if drop {
				removed = true
				continue
			}
			kept = append(kept, url.QueryEscape(key)+"="+url.QueryEscape(value))
		}
		if !removed {
			return raw
		}
		p.query = strings.Join(kept, "&")
		return p.String()
	}
	if host == "tal.net" || strings.HasSuffix(host, ".tal.net") {
		// Lookahead-free replacement keeps the trailing slash available to
		// an immediately following session segment, matching Python regex.
		path := p.path
		for {
			loc := talSegment.FindStringIndex(path)
			if loc == nil {
				break
			}
			path = path[:loc[0]] + path[loc[1]-1:]
		}
		if path == p.path {
			return raw
		}
		p.path = path
		return p.String()
	}
	return raw
}
