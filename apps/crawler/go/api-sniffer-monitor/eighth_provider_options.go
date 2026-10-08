package apisniffer

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// Existing public feed/section/detail resources stay within their configured
// portal. Proxy selection is retained independently of resource admission.
type EighthProviderOptions struct {
	Provider, BoardURL, Origin, Section, Variant string
	FeedURLs                                     []string
	Advertised                                   *int
	Proxy                                        bool
}

var eighthSection = regexp.MustCompile(`^[0-9a-fA-F-]{16,64}$`)
var woowaRecruitID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

func EighthProviderOptionsFromMetadata(provider, source, raw string) (EighthProviderOptions, error) {
	o := EighthProviderOptions{Provider: provider, BoardURL: source}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, parseErr := url.Parse(source)
	if err != nil || parseErr != nil || !validURL(source) || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	o.Origin = "https://" + strings.ToLower(u.Hostname())
	for _, key := range []string{"render", "actions", "skip_ssl", "stealth", "persistent_context"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return o, ErrOptions
	}
	if value, exists := m["proxy"]; exists {
		var ok bool
		o.Proxy, ok = value.(bool)
		if !ok || o.Proxy && provider != "earcu" {
			return o, ErrOptions
		}
	}
	switch provider {
	case "earcu":
		path := strings.TrimRight(u.Path, "/")
		prefixes := []string{}
		if at := strings.Index(strings.ToLower(path), "/vacancy/"); at >= 0 {
			prefixes = append(prefixes, path[:at])
		} else if path != "" {
			prefixes = append(prefixes, "/"+strings.Split(strings.Trim(path, "/"), "/")[0])
		}
		prefixes = append(prefixes, "")
		for _, prefix := range prefixes {
			candidate := o.Origin + prefix + "/allvacancies/"
			if len(o.FeedURLs) == 0 || candidate != o.FeedURLs[0] {
				o.FeedURLs = append(o.FeedURLs, candidate)
			}
		}
		if value, exists := m["feed_url"]; exists && value != nil && value != "" {
			configured, ok := value.(string)
			matched := false
			for _, candidate := range o.FeedURLs {
				matched = matched || configured == candidate
			}
			if !ok || !matched {
				return o, ErrOptions
			}
			o.FeedURLs = []string{configured}
		}
	case "cvwarehouse":
		host := strings.ToLower(u.Hostname())
		if host != "cvw.io" && !strings.HasSuffix(host, ".cvw.io") {
			return o, ErrOptions
		}
		if value, exists := m["section"]; exists && value != nil {
			var ok bool
			o.Section, ok = value.(string)
			if !ok || !eighthSection.MatchString(o.Section) {
				return o, ErrOptions
			}
		}
		if value, exists := m["jobs"]; exists && value != nil {
			n, ok := integer(value)
			if !ok || n < 0 || n > 10000 {
				return o, ErrOptions
			}
			o.Advertised = &n
		}
	case "woowa":
		switch strings.ToLower(u.Hostname()) {
		case "career.woowahan.com":
			o.Variant = "brothers"
		case "career.woowayouths.com":
			o.Variant = "youths"
		case "bmart-career.woowayouths.com":
			o.Variant = "bmart"
		default:
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	return o, nil
}

func (o EighthProviderOptions) Profile() string {
	if o.Provider == "earcu" && o.Proxy {
		return "earcu.proxy-feed-items/v1"
	}
	return o.Provider + ".public-items/v1"
}

func (o EighthProviderOptions) ListingURL() string {
	if o.Provider == "earcu" && len(o.FeedURLs) != 0 {
		return o.FeedURLs[0]
	}
	if o.Provider == "woowa" {
		if o.Variant == "bmart" {
			return o.Origin + "/w1/bmart/recruits"
		}
		return o.Origin + "/w1/recruits"
	}
	return o.BoardURL
}

func (o EighthProviderOptions) SectionURL(source, section string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || !eighthSection.MatchString(section) || !o.ResourceMatches(source) {
		return "", ErrOptions
	}
	q := u.Query()
	q.Set("section", section)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

func (o EighthProviderOptions) WoowaPageURL(page int) string {
	q := url.Values{"category": {"all:all"}, "recruitCampaignSeq": {"0"}, "all": {"all"}, "page": {strconv.Itoa(page)}, "size": {"500"}, "sort": {"updateDate,desc"}}
	return o.ListingURL() + "?" + q.Encode()
}

func (o EighthProviderOptions) ResourceMatches(source string) bool {
	u, err := url.Parse(source)
	if err != nil || !validURL(source) || u.User != nil || u.Fragment != "" || u.Scheme != "https" || "https://"+strings.ToLower(u.Hostname()) != o.Origin || u.Port() != "" && u.Port() != "443" {
		return false
	}
	switch o.Provider {
	case "earcu":
		for _, candidate := range o.FeedURLs {
			if source == candidate {
				return true
			}
		}
	case "cvwarehouse":
		section := u.Query().Get("section")
		return section == "" || eighthSection.MatchString(section)
	case "woowa":
		listing, _ := url.Parse(o.ListingURL())
		if u.Path == listing.Path {
			return true
		}
		if strings.HasPrefix(u.Path, listing.Path+"/") {
			return u.RawQuery == "" && woowaRecruitID.MatchString(strings.TrimPrefix(u.Path, listing.Path+"/"))
		}
	}
	return false
}

// CVWarehouse identifies hosted adverts by a single numeric job query at the
// tenant root. These are adverts even though their path equals the homepage.
func CVWarehousePostingURL(source string) bool {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Path != "/" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host != "cvw.io" && !strings.HasSuffix(host, ".cvw.io") {
		return false
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) != 1 || len(q["job"]) != 1 {
		return false
	}
	id := q.Get("job")
	return id != "" && strings.Trim(id, "0123456789") == ""
}

func (o EighthProviderOptions) WoowaIdentityMatches(source, identity string) bool {
	prefix := "woowa:" + o.Variant + ":"
	if o.Provider != "woowa" || !strings.HasPrefix(identity, prefix) {
		return false
	}
	id := strings.TrimPrefix(identity, prefix)
	if !woowaRecruitID.MatchString(id) {
		return false
	}
	path := "/recruitment/" + id + "/detail"
	if o.Variant == "bmart" {
		path = "/recruitment/detail/" + id
	}
	return source == o.Origin+path
}
