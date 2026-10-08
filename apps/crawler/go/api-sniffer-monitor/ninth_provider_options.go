package apisniffer

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type NinthProviderOptions struct {
	Provider, BoardURL, Origin, Slug, Organization, Locale, Variant, DefaultLocationType string
	Proxy                                                                                bool
}

var ninthSlug = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)
var ninthCompanyPath = regexp.MustCompile(`(?i)^/empresas/ofertas-de-trabajo-de-[a-z0-9][a-z0-9-]*-([0-9a-f]{16})/?$`)
var ninthPandapeHost = regexp.MustCompile(`(?i)^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.pandape\.(?:infojobs\.com\.br|computrabajo\.com)$`)
var ninthHireHiveHost = regexp.MustCompile(`^([a-z0-9-]+)\.hirehive\.com$`)
var ninthWTTJPath = regexp.MustCompile(`(?i)^/([a-z]{2})/companies/([^/]+)(?:/|$)`)

func NinthProviderOptionsFromMetadata(provider, source, raw string) (NinthProviderOptions, error) {
	o := NinthProviderOptions{Provider: provider, BoardURL: source}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, parseErr := url.Parse(source)
	if err != nil || parseErr != nil || !validURL(source) || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	o.Origin = "https://" + host
	for _, key := range []string{"render", "actions", "skip_ssl", "stealth", "persistent_context"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return o, ErrOptions
	}
	if v, exists := m["proxy"]; exists {
		var ok bool
		o.Proxy, ok = v.(bool)
		if !ok || o.Proxy && provider != "computrabajo" {
			return o, ErrOptions
		}
	}
	configured := func(key string) (string, bool) {
		v, exists := m[key]
		if !exists || v == nil || v == "" {
			return "", true
		}
		s, ok := v.(string)
		return s, ok && ninthSlug.MatchString(s)
	}
	switch provider {
	case "ycombinator":
		if host != "ycombinator.com" && host != "www.ycombinator.com" {
			return o, ErrOptions
		}
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		if len(parts) < 3 || parts[0] != "companies" || parts[2] != "jobs" {
			return o, ErrOptions
		}
		o.Slug = parts[1]
		if v, ok := configured("slug"); !ok {
			return o, ErrOptions
		} else if v != "" {
			o.Slug = v
		}
		o.Origin = "https://www.ycombinator.com"
	case "beehire":
		if host != "app.beehire.com" {
			return o, ErrOptions
		}
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		if len(parts) != 2 || !strings.EqualFold(parts[0], "career") && !strings.EqualFold(parts[0], "careerrss") {
			return o, ErrOptions
		}
		o.Slug = strings.ToLower(parts[1])
		if v, ok := configured("slug"); !ok {
			return o, ErrOptions
		} else if v != "" {
			o.Slug = strings.ToLower(v)
		}
	case "hirehive":
		match := ninthHireHiveHost.FindStringSubmatch(host)
		if match == nil {
			return o, ErrOptions
		}
		o.Slug = match[1]
		if v, ok := configured("slug"); !ok {
			return o, ErrOptions
		} else if v != "" {
			o.Slug = v
		}
		switch o.Slug {
		case "api", "app", "assets", "docs", "help", "hirehive-testing-account", "static", "www":
			return o, ErrOptions
		}
		o.Origin = "https://" + o.Slug + ".hirehive.com"
		if v, exists := m["defaults"]; exists && v != nil {
			defaults, ok := v.(map[string]any)
			if !ok {
				return o, ErrOptions
			}
			if v = defaults["job_location_type"]; v != nil {
				o.DefaultLocationType, ok = v.(string)
				if !ok {
					return o, ErrOptions
				}
			}
		}
	case "welcometothejungle":
		if host != "welcometothejungle.com" && host != "www.welcometothejungle.com" {
			return o, ErrOptions
		}
		match := ninthWTTJPath.FindStringSubmatch(u.Path)
		if match == nil {
			return o, ErrOptions
		}
		o.Locale, o.Slug = strings.ToLower(match[1]), match[2]
		for key, target := range map[string]*string{"slug": &o.Slug, "organization_slug": &o.Organization} {
			v, ok := configured(key)
			if !ok {
				return o, ErrOptions
			}
			if v != "" {
				*target = v
			}
		}
		if v, exists := m["locale"]; exists && v != nil {
			var ok bool
			o.Locale, ok = v.(string)
			if !ok || len(o.Locale) != 2 || strings.Trim(o.Locale, "abcdefghijklmnopqrstuvwxyz") != "" {
				return o, ErrOptions
			}
		}
		o.Origin = "https://www.welcometothejungle.com"
	case "computrabajo":
		if u.RawQuery != "" {
			return o, ErrOptions
		}
		if ninthPandapeHost.MatchString(host) {
			o.Variant = "pandape"
			path := strings.ToLower(u.Path)
			if path != "/" && path != "/vacancies" && path != "/vacancies/" && path != "/vacancy/vacancies" && path != "/vacancy/vacancies/" {
				return o, ErrOptions
			}
			if strings.HasSuffix(host, ".pandape.computrabajo.com") && !o.Proxy {
				return o, ErrOptions
			}
		} else if regexp.MustCompile(`^[a-z]{2}\.computrabajo\.com$`).MatchString(host) && ninthCompanyPath.MatchString(u.Path) {
			o.Variant = "employer"
			o.Slug = strings.ToLower(ninthCompanyPath.FindStringSubmatch(u.Path)[1])
		} else {
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	if provider != "computrabajo" && !ninthSlug.MatchString(o.Slug) {
		return o, ErrOptions
	}
	return o, nil
}

func (o NinthProviderOptions) Profile() string {
	switch o.Provider {
	case "ycombinator":
		return "ycombinator.listing-urls/v1"
	case "computrabajo":
		if o.Proxy {
			return "computrabajo.proxy-listing-urls/v1"
		}
		return "computrabajo.listing-urls/v1"
	}
	return o.Provider + ".public-items/v1"
}
func (o NinthProviderOptions) ListingURL() string {
	switch o.Provider {
	case "ycombinator":
		return o.Origin + "/companies/" + o.Slug + "/jobs"
	case "beehire":
		return o.Origin + "/users/getPublicCampaigns/" + o.Slug
	case "hirehive":
		return o.Origin + "/api/v2/jobs"
	case "welcometothejungle":
		return "https://api.welcometothejungle.com/api/v1/organizations/" + o.Slug
	case "computrabajo":
		return o.PageURL(1)
	}
	return ""
}
func (o NinthProviderOptions) PageURL(page int) string {
	if o.Provider == "hirehive" {
		return o.ListingURL() + "?" + url.Values{"page": {strconv.Itoa(page)}, "page_size": {"100"}}.Encode()
	}
	source := o.BoardURL
	if o.Variant == "pandape" {
		u, _ := url.Parse(source)
		if u.Path == "" || u.Path == "/" {
			source = strings.TrimRight(source, "/") + "/Vacancies"
		}
	}
	if page == 1 {
		return source
	}
	param := "p"
	if o.Variant == "pandape" {
		param = "pageNumber"
	}
	return strings.TrimRight(source, "/") + "?" + param + "=" + strconv.Itoa(page)
}
func (o NinthProviderOptions) ResourceMatches(source string) bool {
	u, err := url.Parse(source)
	if err != nil || !validURL(source) || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	switch o.Provider {
	case "ycombinator", "beehire":
		return source == o.ListingURL()
	case "hirehive":
		if source == o.ListingURL() {
			return true
		}
		if "https://"+strings.ToLower(u.Hostname()) != o.Origin || u.Path != "/api/v2/jobs" {
			return false
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) != 2 || len(q["page"]) != 1 || len(q["page_size"]) != 1 || q.Get("page_size") != "100" {
			return false
		}
		n, e := strconv.Atoi(q.Get("page"))
		return e == nil && n >= 1 && n <= 500
	case "computrabajo":
		if source == o.BoardURL || source == o.PageURL(1) {
			return true
		}
		first, _ := url.Parse(o.PageURL(1))
		if !strings.EqualFold(u.Hostname(), first.Hostname()) {
			return false
		}
		if o.Variant == "employer" {
			match := ninthCompanyPath.FindStringSubmatch(u.Path)
			if match == nil || strings.ToLower(match[1]) != o.Slug {
				return false
			}
		} else if !strings.EqualFold(strings.TrimRight(u.Path, "/"), strings.TrimRight(first.Path, "/")) {
			return false
		}
		if u.RawQuery == "" {
			return true
		}
		param := "p"
		if o.Variant == "pandape" {
			param = "pageNumber"
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) != 1 || len(q[param]) != 1 {
			return false
		}
		n, e := strconv.Atoi(q.Get(param))
		return e == nil && n >= 1 && n <= 500
	case "welcometothejungle":
		if source == "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries" || source == o.ListingURL() {
			return true
		}
		prefix := "/api/v1/organizations/" + o.Slug + "/jobs/"
		return u.Hostname() == "api.welcometothejungle.com" && u.RawQuery == "" && strings.HasPrefix(u.Path, prefix) && ninthSlug.MatchString(strings.TrimPrefix(u.Path, prefix))
	}
	return false
}
