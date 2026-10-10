package dom

import (
	"net/url"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
)

var ListingProviderKeys = []string{"lucca_board", "lg_portal", "bunge_bigredsky_board", "vagas_tenant", "hotelcareer_profile", "dualoo_portal", "jobtoolz_tenant", "yousty_organization", "prospective_board", "prospective_canonical_path"}

type ListingProviderProof struct{ Medium, CanonicalPath string }

func listingProviderOptions(config Object) (*ListingProviderProof, error) {
	// These probe markers do not alter original discover requests or extraction.
	// Keep them bound in ownership metadata rather than treating them as options.
	for _, key := range []string{"lucca_board", "lg_portal", "bunge_bigredsky_board"} {
		if v := config[key]; v != nil {
			if _, ok := v.(bool); !ok {
				return nil, ErrListingProof
			}
		}
	}
	for _, key := range []string{"vagas_tenant", "hotelcareer_profile", "dualoo_portal", "jobtoolz_tenant", "yousty_organization"} {
		if v := config[key]; v != nil {
			s, ok := v.(string)
			if !ok || s == "" || !utf8.ValidString(s) || len(s) > 1024 || strings.ContainsRune(s, 0) {
				return nil, ErrListingProof
			}
			if key == "yousty_organization" && !regexp.MustCompile(`^[1-9][0-9]{0,11}-[a-z0-9]+(?:-[a-z0-9]+)*$`).MatchString(s) {
				return nil, ErrListingProof
			}
		}
	}
	if config["prospective_board"] == nil {
		if config["prospective_canonical_path"] != nil {
			return nil, ErrListingProof
		}
		return nil, nil
	}
	medium, ok := config["prospective_board"].(string)
	if !ok || !regexp.MustCompile(`^[1-9][0-9]{0,11}$`).MatchString(medium) {
		return nil, ErrListingProof
	}
	p := &ListingProviderProof{Medium: medium}
	if v := config["prospective_canonical_path"]; v != nil {
		path, ok := v.(string)
		u, e := url.Parse(path)
		if !ok || e != nil || len(path) > 512 || strings.ContainsRune(path, 0) || u.Scheme != "" || u.Host != "" || u.RawQuery != "" || u.Fragment != "" || !strings.HasPrefix(path, "/") || !strings.HasSuffix(path, "/") || !strings.HasSuffix(strings.TrimRight(path, "/"), "/job") {
			return nil, ErrListingProof
		}
		for _, segment := range strings.Split(path, "/") {
			if segment == "." || segment == ".." {
				return nil, ErrListingProof
			}
		}
		p.CanonicalPath = path
	}
	return p, nil
}

func providerOrigin(u *url.URL) string {
	port := u.Port()
	if port == "" {
		port = "443"
	}
	return u.Scheme + "://" + strings.ToLower(u.Hostname()) + ":" + port
}

func (p *ListingProviderProof) Validate(source, board string) error {
	if p == nil {
		return nil
	}
	b, e := url.Parse(board)
	if e != nil || b.Scheme != "https" || b.Hostname() == "" || b.User != nil || b.Port() != "" && b.Port() != "443" {
		return ErrListingProof
	}
	doc, e := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if e != nil {
		return ErrListingProof
	}
	query := func(s string) bool { return cascadia.Query(doc, cascadia.MustCompile(s)) != nil }
	standard := query("body.career-center #jobs-list") && query(".jobs-total .total")
	own := query("body.ownRep form#careercenter-form") && query("body.ownRep .jobsList") && query("#careercenter-form input#offset[value='0']") && query("#careercenter-form input#limit[value]") && query(".chips a.reset.active > span:last-child")
	if !standard && !own {
		return ErrListingProof
	}
	mediums := map[string]bool{}
	asset := regexp.MustCompile(`(?i)/careercenter/([0-9]+)/assets/`)
	for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile("link[href], script[src], img[src]")) {
		raw := icimsAttr(n, "href")
		if raw == "" {
			raw = icimsAttr(n, "src")
		}
		u, e := url.Parse(raw)
		if e != nil {
			continue
		}
		u = b.ResolveReference(u)
		if u.User != nil || providerOrigin(u) != providerOrigin(b) && !(u.Scheme == "https" && strings.EqualFold(u.Hostname(), "ohws.prospective.ch") && (u.Port() == "" || u.Port() == "443")) {
			continue
		}
		if m := asset.FindStringSubmatch(u.EscapedPath()); len(m) == 2 {
			mediums[m[1]] = true
		}
	}
	if len(mediums) != 1 || !mediums[p.Medium] || p.CanonicalPath == "" && query("#jobs-list .job a.job-title[href], .jobsList li a.job[href]") {
		return ErrListingProof
	}
	return nil
}

func (p *ListingProviderProof) Canonicalize(raw, board string) (string, error) {
	if p == nil || p.CanonicalPath == "" {
		return raw, nil
	}
	b, e := url.Parse(board)
	u, err := url.Parse(raw)
	if e != nil || err != nil || u.User != nil || providerOrigin(b) != providerOrigin(u) {
		return "", ErrListingProof
	}
	m := regexp.MustCompile(`(?i)^/(?:[^/?#]+/)+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$`).FindStringSubmatch(u.EscapedPath())
	if len(m) != 2 {
		return "", ErrListingProof
	}
	return b.Scheme + "://" + b.Host + p.CanonicalPath + strings.ToLower(m[1]), nil
}
