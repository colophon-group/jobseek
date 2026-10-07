package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
)

// Public endpoints remain bound to the configured provider, including resources
// discovered through Deel's settings response. Transport overrides are separate
// contracts and cannot silently enter these direct HTTP profiles.
type SeventhProviderOptions struct {
	Provider, Slug, Origin, Organization, Board string
}

var seventhToken = regexp.MustCompile(`^[A-Za-z0-9_-]{1,256}$`)

func SeventhProviderOptionsFromMetadata(provider, source, raw string) (SeventhProviderOptions, error) {
	o := SeventhProviderOptions{Provider: provider}
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return o, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return o, ErrOptions
	}
	u, e := url.Parse(source)
	if e != nil || u.Scheme != "https" || u.User != nil {
		return o, ErrOptions
	}
	text := func(key string) string { s, _ := m[key].(string); return s }
	switch provider {
	case "deel":
		o.Slug, o.Organization, o.Board = text("slug"), text("org_id"), text("board_id")
		if o.Slug == "" {
			if u.Hostname() != "jobs.deel.com" {
				return o, ErrOptions
			}
			p := strings.Split(strings.TrimLeft(u.Path, "/"), "/")
			if len(p) > 1 && p[0] == "job-boards" {
				p = p[1:]
			}
			o.Slug = p[0]
		}
		if !seventhToken.MatchString(o.Slug) {
			return o, ErrOptions
		}
		switch o.Slug {
		case "auth", "login", "signup", "guest", "api", "deelapi", "job-boards", "job-details":
			return o, ErrOptions
		}
		if o.Organization != "" && !seventhToken.MatchString(o.Organization) || o.Board != "" && !seventhToken.MatchString(o.Board) {
			return o, ErrOptions
		}
	case "hibob":
		if text("origin") != "" {
			u, e = url.Parse(text("origin"))
		}
		if e != nil || u.Scheme != "https" || u.User != nil || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".careers.hibob.com") || u.Port() != "" && u.Port() != "443" {
			return o, ErrOptions
		}
		o.Origin = "https://" + strings.ToLower(u.Hostname())
	case "traffit":
		o.Slug = text("slug")
		if o.Slug == "" {
			if !strings.HasSuffix(strings.ToLower(u.Hostname()), ".traffit.com") {
				return o, ErrOptions
			}
			o.Slug = strings.TrimSuffix(strings.ToLower(u.Hostname()), ".traffit.com")
		}
		if !seventhToken.MatchString(o.Slug) {
			return o, ErrOptions
		}
		switch o.Slug {
		case "www", "api", "cdn", "cdn3", "app", "help", "knowledge":
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	return o, nil
}

func (o SeventhProviderOptions) SettingsURL() string {
	return "https://api-prod.letsdeel.com/guest/ats/organizations/" + o.Slug + "/career_page_settings"
}
func (o SeventhProviderOptions) ListingURL() string {
	switch o.Provider {
	case "deel":
		if o.Organization == "" || o.Board == "" {
			return o.SettingsURL()
		}
		return "https://api-prod.letsdeel.com/guest/ats/organizations/" + o.Organization + "/job_boards/" + o.Board + "/job_postings"
	case "hibob":
		return o.Origin + "/api/job-ad"
	case "traffit":
		return "https://" + o.Slug + ".traffit.com/public/job_posts/published"
	}
	return ""
}
func (o SeventhProviderOptions) ResourceMatches(source string) bool {
	if source == o.ListingURL() || o.Provider == "deel" && source == o.SettingsURL() {
		return true
	}
	if o.Provider != "deel" || o.Organization != "" && o.Board != "" {
		return false
	}
	u, e := url.Parse(source)
	if e != nil || u.Scheme != "https" || u.Host != "api-prod.letsdeel.com" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	p := strings.Split(u.EscapedPath(), "/")
	return len(p) == 8 && p[1] == "guest" && p[2] == "ats" && p[3] == "organizations" && seventhToken.MatchString(p[4]) && p[5] == "job_boards" && seventhToken.MatchString(p[6]) && p[7] == "job_postings"
}
func (o SeventhProviderOptions) WithSettings(d *Document) (SeventhProviderOptions, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrInventory
	}
	b, _ := m["jobBoard"].(map[string]any)
	o.Organization, _ = m["organizationId"].(string)
	o.Board, _ = b["id"].(string)
	if !seventhToken.MatchString(o.Organization) || !seventhToken.MatchString(o.Board) {
		return o, ErrInventory
	}
	return o, nil
}
