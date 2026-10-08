package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
)

type TenthProviderOptions struct {
	Provider, BoardURL, Origin, Slug, Alias, Locale, BoardID, Language, ConfiguredAPI string
}

var tenthLocale = regexp.MustCompile(`^[a-z]{2}(?:-[a-z]{2})?$`)
var tenthUniversiaSlug = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$`)
var tenthUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

func TenthProviderOptionsFromMetadata(provider, board, raw string) (TenthProviderOptions, error) {
	o := TenthProviderOptions{Provider: provider, BoardURL: board, Locale: "en"}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, urlErr := url.Parse(board)
	if err != nil || urlErr != nil || !validURL(board) || u.User != nil || u.Port() != "" && u.Port() != "443" && u.Port() != "80" {
		return o, ErrOptions
	}
	if u.Scheme != "https" && (provider != "typify" || u.Scheme != "http") {
		return o, ErrOptions
	}
	o.Origin = u.Scheme + "://" + strings.ToLower(u.Host)
	allowed := map[string]bool{}
	switch provider {
	case "intervieweb":
		host := strings.ToLower(u.Hostname())
		if host != "intervieweb.it" && !strings.HasSuffix(host, ".intervieweb.it") || u.Port() != "" && u.Port() != "443" {
			return o, ErrOptions
		}
		allowed["provider"] = true
		if v := m["provider"]; v != nil {
			if text, ok := v.(string); !ok || len(text) > 160 {
				return o, ErrOptions
			}
		}
	case "typify":
		allowed["api_url"] = true
		if v := m["api_url"]; detailTruthy(v) {
			var ok bool
			o.ConfiguredAPI, ok = v.(string)
			if !ok || !validURL(o.ConfiguredAPI) {
				return o, ErrOptions
			}
		}
	case "universia":
		if strings.ToLower(u.Hostname()) != "jobboard.universia.net" || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" {
			return o, ErrOptions
		}
		o.Slug = strings.ToLower(strings.Trim(u.EscapedPath(), "/"))
		if !tenthUniversiaSlug.MatchString(o.Slug) {
			return o, ErrOptions
		}
		allowed["board_id"], allowed["language"] = true, true
		if v := m["board_id"]; v != nil {
			o.BoardID, _ = v.(string)
			o.BoardID = strings.ToLower(o.BoardID)
			if !tenthUUID.MatchString(o.BoardID) {
				return o, ErrOptions
			}
		}
		if v := m["language"]; v != nil {
			o.Language, _ = v.(string)
			if !regexp.MustCompile(`^[a-z]{2}$`).MatchString(o.Language) {
				o.Language = ""
			}
		}
	case "talentreef":
		allowed["alias"], allowed["locale"] = true, true
		parts := strings.FieldsFunc(u.EscapedPath(), func(r rune) bool { return r == '/' })
		if strings.ToLower(u.Hostname()) == "apply.jobappnetwork.com" && (u.Port() == "" || u.Port() == "443") && len(parts) >= 1 && len(parts) <= 2 && ninthSlug.MatchString(parts[0]) {
			locale := "en"
			if len(parts) == 2 {
				locale = strings.ToLower(parts[1])
			}
			if tenthLocale.MatchString(locale) {
				o.Alias, o.Locale = strings.ToLower(parts[0]), locale
			}
		}
		if v := m["alias"]; detailTruthy(v) {
			o.Alias, _ = v.(string)
			o.Alias = strings.ToLower(o.Alias)
		}
		if v := m["locale"]; detailTruthy(v) {
			o.Locale, _ = v.(string)
			o.Locale = strings.ToLower(o.Locale)
		}
		if !ninthSlug.MatchString(o.Alias) || !tenthLocale.MatchString(o.Locale) {
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	for key := range m {
		switch key {
		case "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor":
		default:
			if !allowed[key] {
				return o, ErrOptions
			}
		}
	}
	return o, nil
}

func (o TenthProviderOptions) Profile() string {
	switch o.Provider {
	case "intervieweb":
		return "intervieweb.form-listing-urls/v1"
	case "typify":
		return "typify.partition-items/v1"
	case "universia":
		return "universia.public-items/v1"
	case "talentreef":
		return "talentreef.brand-items/v1"
	}
	return ""
}

func (o TenthProviderOptions) ListingURL() string {
	switch o.Provider {
	case "universia":
		return "https://api-manager.universia.net/empleo/entities/v2/jobboard/slug/" + o.Slug + "/config/"
	case "talentreef":
		return "https://prod-kong.internal.talentreef.com/apply/careerPages/alias/" + o.Alias
	}
	return o.BoardURL
}

func (o TenthProviderOptions) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" && u.Port() != "80" {
		return false
	}
	if raw == o.BoardURL || strings.TrimRight(raw, "/") == strings.TrimRight(o.ListingURL(), "/") {
		return true
	}
	switch o.Provider {
	case "intervieweb":
		return u.Scheme+"://"+strings.ToLower(u.Host) == o.Origin && strings.HasSuffix(u.Path, "/app.php") && u.Query().Get("module") == "newcareer"
	case "typify":
		board, _ := url.Parse(o.BoardURL)
		return u.Scheme == "https" && strings.EqualFold(u.Host, board.Host) && regexp.MustCompile(`^/(?:[a-z]{1,8}/)?api/vacancies$`).MatchString(u.Path)
	case "universia":
		return u.Scheme == "https" && u.Host == "api-manager.universia.net" && (u.Path == "/empleo/entities/v2/jobboard/slug/"+o.Slug+"/config/" || u.Path == "/orientacion-job-posting/v1/api/job-posting")
	case "talentreef":
		return u.Scheme == "https" && u.Host == "prod-kong.internal.talentreef.com" && (u.Path == "/apply/careerPages/alias/"+o.Alias || regexp.MustCompile(`^/apply/proxy-es/search-[a-z]{2}(?:-[a-z]{2})?/posting/_search$`).MatchString(u.Path))
	}
	return false
}
