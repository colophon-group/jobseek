package apisniffer

import (
	"math/big"
	"net/url"
	"regexp"
	"strings"
	"unicode"
)

type SoftgardenOptions struct{ Slug, Pattern string }

var softgardenIDs = regexp.MustCompile(`var\s+complete_job_id_list\s*=\s*(?:jobs_selected\s*=\s*)?\[([^\]]*)\]`)
var softgardenSlug = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$`)
var softgardenInteger = regexp.MustCompile(`^[+-]?[0-9](?:_?[0-9])*$`)

func softgardenDecimal(token string) string {
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		for _, v := range unicode.Digit.R16 {
			if uint32(r) >= uint32(v.Lo) && uint32(r) <= uint32(v.Hi) && (uint32(r)-uint32(v.Lo))%uint32(v.Stride) == 0 {
				return rune('0' + ((uint32(r)-uint32(v.Lo))/uint32(v.Stride))%10)
			}
		}
		for _, v := range unicode.Digit.R32 {
			if uint32(r) >= v.Lo && uint32(r) <= v.Hi && (uint32(r)-v.Lo)%v.Stride == 0 {
				return rune('0' + ((uint32(r)-v.Lo)/v.Stride)%10)
			}
		}
		return r
	}, token)
}

func SoftgardenOptionsFromMetadata(source, raw string) (SoftgardenOptions, error) {
	md, err := DecodeInlineMetadata(raw)
	if err != nil || !validURL(source) {
		return SoftgardenOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(md[key]) {
			return SoftgardenOptions{}, ErrOptions
		}
	}
	if md["ssl_verify"] != nil && md["ssl_verify"] != true {
		return SoftgardenOptions{}, ErrOptions
	}
	slug, _ := md["slug"].(string)
	if !detailTruthy(md["slug"]) {
		u, _ := url.Parse(source)
		host := strings.ToLower(u.Hostname())
		if strings.HasSuffix(host, ".softgarden.io") {
			slug = strings.TrimSuffix(host, ".softgarden.io")
		}
	}
	if !softgardenSlug.MatchString(slug) || slug == "www" || slug == "api" || slug == "app" || slug == "static" || slug == "cdn" {
		return SoftgardenOptions{}, ErrOptions
	}
	pattern := "{base}/job/{id}?l=en"
	if value, present := md["job_url_pattern"]; present {
		var ok bool
		pattern, ok = value.(string)
		if !ok {
			return SoftgardenOptions{}, ErrOptions
		}
	}
	o := SoftgardenOptions{slug, pattern}
	if len(pattern) > 8192 || !validURL(o.JobURL("1")) {
		return SoftgardenOptions{}, ErrOptions
	}
	return o, nil
}
func (o SoftgardenOptions) ListingURL() string { return "https://" + o.Slug + ".softgarden.io" }
func (o SoftgardenOptions) JobURL(id string) string {
	return strings.ReplaceAll(strings.ReplaceAll(o.Pattern, "{base}", o.ListingURL()), "{id}", id)
}
func (o SoftgardenOptions) ResourceMatches(source string) bool { return source == o.ListingURL() }

// Preserve Python int token acceptance and arbitrary precision before URL
// construction; the existing global writer deduplicates canonical identities.
func SoftgardenListing(source string, o SoftgardenOptions) ([]string, bool, error) {
	if o.Slug == "" || !validURL(o.ListingURL()) {
		return nil, false, ErrOptions
	}
	match := softgardenIDs.FindStringSubmatch(source)
	out := []string{}
	if len(match) < 2 {
		return out, false, nil
	}
	count := 0
	seen := map[string]bool{}
	for _, token := range strings.Split(match[1], ",") {
		token = strings.TrimSpace(token)
		if token == "" {
			continue
		}
		token = softgardenDecimal(token)
		if !softgardenInteger.MatchString(token) {
			continue
		}
		token = strings.ReplaceAll(token, "_", "")
		if len(strings.TrimLeft(token, "+-")) > 4300 {
			continue
		}
		n, ok := new(big.Int).SetString(token, 10)
		if !ok {
			continue
		}
		count++
		value := o.JobURL(n.String())
		if !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	return out, count > 50000, nil
}
