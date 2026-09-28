package dom

import (
	"errors"
	"strings"
	"unicode/utf8"
)

// ClassifyRendered applies the canonical rendered DOM policy before parsing.
// Gone redirects precede challenge detection. Invalid regex syntax is ignored,
// as in the Python scraper; a bounded regex execution failure remains an error.
func ClassifyRendered(html string, config Object, finalURL string) (Object, error) {
	if len(html) > 1<<20 || len(finalURL) > 8192 || !utf8.ValidString(html) || !utf8.ValidString(finalURL) {
		return nil, errors.New("invalid rendered document bounds")
	}
	if pattern, ok := config["gone_url_pattern"].(string); ok && pattern != "" && finalURL != "" {
		cache := regexCache{}
		if re, err := cache.compile(pattern, false); err == nil {
			match, err := re.MatchString(finalURL)
			if err != nil {
				return nil, errors.New("gone redirect regex execution failed")
			}
			if match {
				return Object{"classification": "gone"}, nil
			}
		}
	}
	// Python lower expands dotted capital I; simple Go lower would incorrectly
	// turn it into an ASCII marker character.
	text := strings.ToLower(strings.ReplaceAll(finalURL+"\n"+html, "\u0130", "i\u0307"))
	any := func(markers ...string) bool {
		for _, marker := range markers {
			if strings.Contains(text, marker) {
				return true
			}
		}
		return false
	}
	all := func(markers ...string) bool {
		for _, marker := range markers {
			if !strings.Contains(text, marker) {
				return false
			}
		}
		return true
	}
	challenge := any("/.well-known/captcha", "/.well-known/sgcaptcha", "<title>just a moment") ||
		(all("/cdn-cgi/challenge-platform/") && any("enable javascript and cookies", "sorry, you have been blocked")) ||
		any("please wait while your request is being verified") ||
		all(`id="main-iframe"`, "/_incapsula_resource?cwudnsai=") ||
		any("validate.perfdrive.com", "<title>radware captcha page", "botmanager_support@radware.com", "captcha.perfdrive.com/captcha-public/") ||
		all("<title>validation request</title>", "user validation required to continue", `action="/captcha_resp"`) ||
		all("safe.liepin.com/", "captchapage_ip_pc")
	if challenge {
		return Object{"classification": "challenge"}, nil
	}
	return Object{"classification": "okay"}, nil
}
