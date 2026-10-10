package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const CandidatusHTTPProfile = "candidatus.http-postback-urls/v1"

type CandidatusConfig struct {
	ListingURL string
	MaxJobs    int
}

var candidatusListingPath = regexp.MustCompile(`^/site-emploi,[A-Za-z0-9_-]+(?:;[A-Za-z0-9_-]+)*/?$`)
var candidatusDetailPath = regexp.MustCompile(`^/annonce-emploi,[^/?#]+$`)

func CandidatusMonitorOptions(config map[string]string) (CandidatusConfig, error) {
	fail := func() (CandidatusConfig, error) { return CandidatusConfig{}, ErrUnsupportedProfile }
	if config["crawler_type"] != "candidatus" || config["monitor_needs_browser"] != "1" || config["scraper_needs_browser"] != "0" {
		return fail()
	}
	u, e := url.Parse(config["board_url"])
	if e != nil || u.Scheme != "https" || u.User != nil || u.Hostname() != "carrieres.candidatus.com" || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" || !candidatusListingPath.MatchString(u.EscapedPath()) {
		return fail()
	}
	md, e := richProfileMetadata(config)
	if e != nil {
		return fail()
	}
	for _, key := range []string{"proxy", "skip_ssl"} {
		if raw := md[key]; raw != nil && string(raw) != "false" && string(raw) != "null" {
			return fail()
		}
	}
	if raw := md["ssl_verify"]; raw != nil && string(raw) != "true" && string(raw) != "null" {
		return fail()
	}
	if raw := md["wait"]; raw != nil && string(raw) != `"domcontentloaded"` && string(raw) != "null" {
		return fail()
	}
	if raw := md["timeout"]; raw != nil && string(raw) != "30000" && string(raw) != "null" {
		return fail()
	}
	c := CandidatusConfig{ListingURL: config["board_url"], MaxJobs: 1000}
	if raw := md["max_jobs"]; raw != nil {
		text := string(raw)
		var quoted string
		if json.Unmarshal(raw, &quoted) == nil {
			text = quoted
		}
		n, e := strconv.Atoi(text)
		if e != nil || n < 1 || n > 1000 {
			return fail()
		}
		c.MaxJobs = n
	}
	return c, nil
}

func (c CandidatusConfig) ResourceMatches(raw string) bool {
	if raw == c.ListingURL {
		return true
	}
	base, e := url.Parse(c.ListingURL)
	u, other := url.Parse(raw)
	if e != nil || other != nil || u.Scheme != base.Scheme || u.Host != base.Host || u.EscapedPath() != base.EscapedPath() || u.User != nil || u.Fragment != "" {
		return false
	}
	values, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(values) != 5 {
		return false
	}
	for _, key := range []string{"id", "lang", "intra", "v", "rub"} {
		if len(values[key]) != 1 {
			return false
		}
	}
	id := strings.TrimSuffix(strings.TrimPrefix(base.EscapedPath(), "/site-emploi,"), "/")
	return values.Get("id") == id && values.Get("lang") == "" && values.Get("intra") == "" && values.Get("v") == "" && values.Get("rub") == ""
}

func CandidatusCanonicalDetailURL(raw string) (string, error) {
	u, e := url.Parse(raw)
	if e != nil || !strings.EqualFold(u.Scheme, "https") || !strings.EqualFold(u.Hostname(), "carrieres.candidatus.com") || u.User != nil || u.Port() != "" && u.Port() != "443" || !candidatusDetailPath.MatchString(u.EscapedPath()) {
		return "", ErrUnsupportedProfile
	}
	return "https://carrieres.candidatus.com" + u.EscapedPath(), nil
}
