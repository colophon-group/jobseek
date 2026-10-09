package apisniffer

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

type KekaBoard struct {
	Tenant, Portal, Identifier string
}

func (b KekaBoard) ListingURL() string {
	suffix := ""
	if b.Portal != "default" {
		suffix = "/" + b.Portal
	}
	return "https://" + b.Tenant + ".keka.com/careers" + suffix
}

func (b KekaBoard) JobsURL() string {
	return "https://" + b.Tenant + ".keka.com/careers/api/embedjobs/" + b.Portal + "/active/" + b.Identifier
}

var kekaTenantPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var kekaPortalPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)
var kekaIdentifierPattern = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var kekaBootstrapPattern = regexp.MustCompile(`(?i)fetch\(\s*['"](/ats/documents/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/careerportal/[0-9a-f]{32}\.html)['"]\s*\)`)
var turboHireCareerPath = regexp.MustCompile(`(?i)^/careerpage/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$`)

func kekaTenant(value any) string {
	s := strings.ToLower(smallText(value))
	if !kekaTenantPattern.MatchString(s) {
		return ""
	}
	switch s {
	case "api", "app", "help", "static", "support", "www":
		return ""
	}
	return s
}

func kekaPortal(value any) string {
	if value == nil {
		return "default"
	}
	if _, ok := value.(string); !ok {
		return ""
	}
	s := strings.ToLower(smallText(value))
	if s == "" || s == "default" {
		return "default"
	}
	if !kekaPortalPattern.MatchString(s) || s == "api" || s == "content" || s == "jobdetails" {
		return ""
	}
	return s
}

func KekaBoardFromURL(source string) (KekaBoard, bool) {
	u, err := url.Parse(source)
	if err != nil || len(source) > 4096 || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".keka.com") {
		return KekaBoard{}, false
	}
	tenant := kekaTenant(strings.TrimSuffix(strings.ToLower(u.Hostname()), ".keka.com"))
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) > 1 || len(q) == 1 && (len(q["source"]) != 1 || q.Get("source") == "") || tenant == "" {
		return KekaBoard{}, false
	}
	parts := strings.FieldsFunc(u.EscapedPath(), func(r rune) bool { return r == '/' })
	if len(parts) == 0 || !strings.EqualFold(parts[0], "careers") {
		return KekaBoard{}, false
	}
	portal := "default"
	switch {
	case len(parts) == 1:
	case len(parts) == 2:
		portal = kekaPortal(parts[1])
	case len(parts) == 3 && strings.EqualFold(parts[1], "jobdetails") && portalPositiveID.MatchString(parts[2]):
	case len(parts) == 4 && strings.EqualFold(parts[2], "jobdetails") && portalPositiveID.MatchString(parts[3]):
		portal = kekaPortal(parts[1])
	default:
		return KekaBoard{}, false
	}
	return KekaBoard{Tenant: tenant, Portal: portal}, portal != ""
}

func KekaBoardFromMetadata(metadata map[string]any) (KekaBoard, bool) {
	tenant, portal := kekaTenant(metadata["tenant"]), kekaPortal(metadata["portal"])
	identifier := ""
	if raw := metadata["identifier"]; raw != nil {
		identifier = strings.ToLower(smallText(raw))
		if !kekaIdentifierPattern.MatchString(identifier) {
			return KekaBoard{}, false
		}
	}
	return KekaBoard{Tenant: tenant, Portal: portal, Identifier: identifier}, tenant != "" && portal != ""
}

func KekaBootstrapIdentifier(body []byte) string {
	if match := kekaBootstrapPattern.FindStringSubmatch(string(body)); match != nil {
		return strings.ToLower(match[2])
	}
	return ""
}

type PortalHTTPOptions struct {
	Provider, BoardURL, Origin, Listing, Employer, Organization string
	Keka                                                        KekaBoard
	PageUp                                                      PageUpBoard
}

func PortalHTTPProviderOptionsFromMetadata(provider, board, raw string) (PortalHTTPOptions, error) {
	out := PortalHTTPOptions{Provider: provider, BoardURL: board}
	d, err := Decode([]byte(raw))
	if err != nil || len(raw) > 1<<20 {
		return out, ErrOptions
	}
	metadata, ok := d.Value.(map[string]any)
	u, err := url.Parse(board)
	if !ok || err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Port() != "" && u.Port() != "443" || len(board) > 8192 {
		return out, ErrOptions
	}
	out.Origin = "https://" + strings.ToLower(u.Hostname())
	switch provider {
	case "keka":
		direct, hasDirect := KekaBoardFromURL(board)
		configured, hasConfigured := KekaBoardFromMetadata(metadata)
		hasIdentity := false
		for _, key := range []string{"tenant", "portal", "identifier"} {
			if _, exists := metadata[key]; exists {
				hasIdentity = true
			}
		}
		if hasIdentity && !hasConfigured || hasConfigured && hasDirect && (configured.Tenant != direct.Tenant || configured.Portal != direct.Portal) || !hasDirect && !hasConfigured {
			return out, ErrOptions
		}
		out.Keka = direct
		if hasConfigured {
			out.Keka = configured
		}
		out.Origin = "https://" + out.Keka.Tenant + ".keka.com"
		out.Listing = out.Keka.ListingURL()
	case "pageup":
		direct, hasDirect := PageUpBoardFromURL(board)
		configured, hasConfigured := PageUpBoardFromMetadata(metadata)
		hasIdentity := false
		for _, key := range []string{"instance", "source_pointer", "locale", "listing_url"} {
			if _, exists := metadata[key]; exists {
				hasIdentity = true
			}
		}
		if hasIdentity && !hasConfigured || hasConfigured && hasDirect && configured != direct || !hasConfigured && !hasDirect {
			return out, ErrOptions
		}
		out.PageUp = direct
		if hasConfigured {
			out.PageUp = configured
		}
		out.Origin, out.Listing = "https://careers.pageuppeople.com", out.PageUp.PageURL(1, 500)
	case "infoniqa":
		origin, listing, err := InfoniqaBoardURLs(board)
		employer, ok := metadata["employer_name"].(string)
		if err != nil || !ok || employer == "" || employer != inlineTrim(employer) || len([]rune(employer)) > 200 {
			return out, ErrOptions
		}
		out.Origin, out.Listing, out.Employer = origin, listing, employer
	case "turbohire":
		if !strings.HasSuffix(strings.ToLower(u.Hostname()), ".turbohire.co") {
			return out, ErrOptions
		}
		configured := ""
		if value, ok := metadata["org_id"].(string); ok && payNetUUID.MatchString(strings.ToLower(value)) {
			configured = strings.ToLower(value)
		}
		direct := ""
		if match := turboHireCareerPath.FindStringSubmatch(u.Path); match != nil {
			direct = strings.ToLower(match[1])
		} else {
			for _, key := range []string{"orgId", "orgid"} {
				if value := u.Query().Get(key); payNetUUID.MatchString(strings.ToLower(value)) {
					direct = strings.ToLower(value)
					break
				}
			}
		}
		if configured != "" && direct != "" && configured != direct || configured == "" && direct == "" {
			return out, ErrOptions
		}
		out.Organization = direct
		if configured != "" {
			out.Organization = configured
		}
		out.Listing = "https://thapi.azurewebsites.net/api/token/noauth"
	default:
		return out, ErrOptions
	}
	return out, nil
}

func (o PortalHTTPOptions) Profile() string {
	switch o.Provider {
	case "pageup":
		return "pageup.listing-items/v1"
	case "infoniqa":
		return "infoniqa.session-urls/v1"
	case "keka", "turbohire":
		return o.Provider + ".public-items/v1"
	}
	return ""
}

func (o PortalHTTPOptions) ResourceMatches(resource string) bool {
	u, err := url.Parse(resource)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	switch o.Provider {
	case "keka":
		if resource == o.Keka.ListingURL() {
			return true
		}
		prefix := o.Origin + "/careers/api/embedjobs/" + o.Keka.Portal + "/active/"
		return strings.HasPrefix(resource, prefix) && kekaIdentifierPattern.MatchString(strings.TrimPrefix(resource, prefix)) && (o.Keka.Identifier == "" || resource == o.Keka.JobsURL())
	case "pageup":
		identity, ok := pageupPageIdentity(resource, o.PageUp)
		return ok && identity[0] >= 1 && identity[0] <= 100 && identity[1] == 500
	case "infoniqa":
		return resource == o.BoardURL || resource == o.Listing || resource == o.Listing+"?search=true"
	case "turbohire":
		if u.Host != "thapi.azurewebsites.net" {
			return false
		}
		q, err := url.ParseQuery(u.RawQuery)
		if err != nil {
			return false
		}
		for _, values := range q {
			if len(values) != 1 {
				return false
			}
		}
		switch u.Path {
		case "/api/token/noauth":
			return len(q) == 0
		case "/api/careerpagev2/filteredjobs":
			return len(q) == 2 && q.Get("orgId") == o.Organization && q.Get("pageType") == "0"
		case "/api/publicjobs":
			return len(q) == 2 && q.Get("jobId") != "" && len(q.Get("jobId")) <= 4096 && q.Get("fieldVisibility") == "0"
		}
	}
	return false
}

func portalMarshal(value any) (string, error) {
	body, err := json.Marshal(value)
	return string(body), err
}
