package apisniffer

import (
	"net/http"
	"regexp"
	"strings"
)

var sessionCookieName = regexp.MustCompile("^[!#$%&'*+\\-.^_`|~0-9A-Za-z]+$")

// OriginalSessionCookies preserves shared/http.py's RFC 6265 policy. Go's
// standard parser accepts spaces/commas which the original crawler rejects.
// Inspect each raw header so a bare cookie (Python's None value) stays rejected.
func OriginalSessionCookies(headers http.Header) []*http.Cookie {
	out := []*http.Cookie{}
	for _, raw := range headers.Values("Set-Cookie") {
		first, _, _ := strings.Cut(raw, ";")
		if !strings.Contains(first, "=") {
			continue
		}
		response := &http.Response{Header: http.Header{"Set-Cookie": []string{raw}}}
		for _, cookie := range response.Cookies() {
			if !sessionCookieName.MatchString(cookie.Name) {
				continue
			}
			valid := true
			for _, b := range []byte(cookie.Value) {
				if b != 0x21 && !(b >= 0x23 && b <= 0x2b) && !(b >= 0x2d && b <= 0x3a) && !(b >= 0x3c && b <= 0x5b) && !(b >= 0x5d && b <= 0x7e) {
					valid = false
					break
				}
			}
			if valid {
				out = append(out, cookie)
			}
		}
	}
	return out
}
