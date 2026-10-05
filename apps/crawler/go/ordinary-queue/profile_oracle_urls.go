package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

// OracleURLRules covers the enabled non-collision Oracle URL policy. It is
// extraction configuration; it grants no request, queue or persistence authority.
type OracleURLRules struct {
	allow, find *regexp.Regexp
	replacement string
}

func OracleMonitorURLRules(config map[string]string) (*OracleURLRules, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	rules := &OracleURLRules{}
	if raw, ok := md["url_allowlist"]; ok {
		var pattern string
		if json.Unmarshal(raw, &pattern) != nil || pattern == "" || len(pattern) > 2048 {
			return nil, ErrUnsupportedProfile
		}
		rules.allow, err = regexp.Compile(`\A(?:` + pattern + `)\z`)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
	}
	if raw, ok := md["url_transform"]; ok && string(raw) != "null" {
		options, err := profileMetadataFields(string(raw), map[string]bool{"find": true, "replace": true})
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
		var pattern, replacement string
		if json.Unmarshal(options["find"], &pattern) != nil || pattern == "" || len(pattern) > 2048 || json.Unmarshal(options["replace"], &replacement) != nil || len(replacement) > 4096 {
			return nil, ErrUnsupportedProfile
		}
		if strings.Contains(replacement, "{identity}") {
			return nil, ErrUnsupportedProfile
		}
		rules.find, err = regexp.Compile(pattern)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
		var compiled strings.Builder
		for n := 0; n < len(replacement); n++ {
			switch replacement[n] {
			case '$':
				compiled.WriteString("$$")
			case '\\':
				n++
				if n >= len(replacement) || replacement[n] < '1' || replacement[n] > '9' || int(replacement[n]-'0') > rules.find.NumSubexp() {
					return nil, ErrUnsupportedProfile
				}
				// Python reads consecutive digits as one group reference. This
				// supported subset deliberately admits single-digit groups only.
				if n+1 < len(replacement) && replacement[n+1] >= '0' && replacement[n+1] <= '9' {
					return nil, ErrUnsupportedProfile
				}
				compiled.WriteString("${")
				compiled.WriteByte(replacement[n])
				compiled.WriteByte('}')
			default:
				compiled.WriteByte(replacement[n])
			}
		}
		rules.replacement = compiled.String()
		// Only the enabled complete absolute-URL rewrite is admitted here. Other
		// templates and collision policies retain their existing owner.
		probe := regexp.MustCompile(`\$\{[1-9]\}`).ReplaceAllString(rules.replacement, "123")
		u, err := url.Parse(probe)
		if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Port() != "" || u.Opaque != "" {
			return nil, ErrUnsupportedProfile
		}
	}
	return rules, nil
}

func (r *OracleURLRules) Apply(source string) (string, error) {
	if r == nil {
		return "", ErrUnsupportedProfile
	}
	if r.allow != nil && !r.allow.MatchString(source) {
		return "", ErrUnsupportedProfile
	}
	if r.find != nil {
		source = r.find.ReplaceAllString(source, r.replacement)
	}
	return source, nil
}
