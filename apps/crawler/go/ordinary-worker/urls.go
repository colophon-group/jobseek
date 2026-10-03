package worker

import (
	"net"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

type pythonURL struct{ scheme, host, path, params, query, fragment string }

var urlScheme = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*$`)
var ipvFuture = regexp.MustCompile(`^v[0-9A-Fa-f]+\..+$`)
var talSegment = regexp.MustCompile(`/xf-[a-f0-9]+/`)
var paramSchemes = map[string]bool{"": true, "ftp": true, "hdl": true, "prospero": true, "http": true, "imap": true, "https": true, "shttp": true, "rtsp": true, "rtsps": true, "rtspu": true, "sip": true, "sips": true, "mms": true, "sftp": true, "tel": true}
var volatileSuccessFactors = map[string]bool{"_s.crb": true, "jobAlertController_jobAlertId": true, "jobAlertController_jobAlertName": true, "browserTimeZone": true}

// Classification/canonicalization use Python urllib's decomposition, rather
// than Go's stricter escaped-path parser. Unmodified URLs retain their exact
// bytes, including invalid percent escapes accepted by the Python URL lane.
func parsePythonURL(raw string) (pythonURL, bool) {
	s := strings.TrimLeftFunc(raw, func(r rune) bool { return r <= 32 })
	s = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(s)
	p := pythonURL{}
	if colon := strings.IndexByte(s, ':'); colon >= 0 && urlScheme.MatchString(s[:colon]) {
		p.scheme = strings.ToLower(s[:colon])
		s = s[colon+1:]
	}
	if strings.HasPrefix(s, "//") {
		s = s[2:]
		end := strings.IndexAny(s, "/?#")
		if end < 0 {
			end = len(s)
		}
		p.host, s = s[:end], s[end:]
		if strings.ContainsAny(p.host, "[]") {
			host := p.host
			if at := strings.LastIndexByte(host, '@'); at >= 0 {
				host = host[at+1:]
			}
			open, close := strings.IndexByte(host, '['), strings.IndexByte(host, ']')
			if open != 0 || close < open || (close+1 < len(host) && host[close+1] != ':') {
				return pythonURL{}, false
			}
			bracket := host[open+1 : close]
			if !ipvFuture.MatchString(bracket) {
				address := bracket
				if scope := strings.IndexByte(address, '%'); scope >= 0 {
					address = address[:scope]
				}
				if ip := net.ParseIP(address); ip == nil || !strings.Contains(address, ":") {
					return pythonURL{}, false
				}
			}
		}
		checked := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(p.host)
		if normalized := norm.NFKC.String(checked); normalized != checked && strings.ContainsAny(normalized, "/?#@:") {
			return pythonURL{}, false
		}
	}
	if fragment := strings.IndexByte(s, '#'); fragment >= 0 {
		p.fragment, s = s[fragment+1:], s[:fragment]
	}
	if query := strings.IndexByte(s, '?'); query >= 0 {
		p.query, s = s[query+1:], s[:query]
	}
	p.path = s
	if paramSchemes[p.scheme] {
		start := strings.LastIndexByte(s, '/') + 1
		if semi := strings.IndexByte(s[start:], ';'); semi >= 0 {
			index := start + semi
			p.path, p.params = s[:index], s[index+1:]
		}
	}
	return p, true
}

func (p pythonURL) String() string {
	path := p.path
	if p.params != "" {
		path += ";" + p.params
	}
	result := ""
	if p.scheme != "" {
		result = p.scheme + ":"
	}
	if p.host != "" {
		result += "//" + p.host
		if path != "" && !strings.HasPrefix(path, "/") {
			result += "/"
		}
	}
	result += path
	if p.query != "" {
		result += "?" + p.query
	}
	if p.fragment != "" {
		result += "#" + p.fragment
	}
	return result
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
		return "bare_host"
	}
	if bp, ok := parsePythonURL(board); ok && strings.EqualFold(bp.host, p.host) && strings.TrimRight(bp.path, "/") == path && p.query == "" {
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
