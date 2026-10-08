package apisniffer

import (
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var ninthYCJobs = regexp.MustCompile(`"/companies/([\pL\pN_-]+)/jobs/([A-Za-z0-9]+-[^"]+)"`)
var ninthEmployerJobs = regexp.MustCompile(`(?i)^/ofertas-de-trabajo/oferta-de-trabajo-de-[^/?#]+-[0-9a-f]{32}/?$`)
var ninthPandapeJobs = regexp.MustCompile(`(?i)^/detail/[1-9][0-9]{0,15}/?$`)
var ninthEmployerCount = regexp.MustCompile(`(?i)^\s*([0-9][0-9.,]*)\s+ofertas?\s+de\s+trabajo\b`)
var ninthPandapeCount = regexp.MustCompile(`(?i)^\s*([0-9][0-9.,]*)\s+(?:vagas|ofertas)\s+de\s+(?:emprego|empleo)\s*$`)

func YCombinatorListingURLs(body string, o NinthProviderOptions) ([]string, error) {
	if o.Provider != "ycombinator" || o.Slug == "" {
		return nil, ErrOptions
	}
	seen := map[string]bool{}
	for _, m := range ninthYCJobs.FindAllStringSubmatch(body, -1) {
		if m[1] == o.Slug {
			seen[o.Origin+"/companies/"+o.Slug+"/jobs/"+m[2]] = true
		}
	}
	out := make([]string, 0, len(seen))
	for source := range seen {
		out = append(out, source)
	}
	sort.Strings(out)
	return out, nil
}

func ComputrabajoJobURL(source string, o NinthProviderOptions) (string, error) {
	base, err := url.Parse(o.BoardURL)
	u, parseErr := url.Parse(source)
	if err != nil || parseErr != nil || o.Provider != "computrabajo" {
		return "", ErrOptions
	}
	u = base.ResolveReference(u)
	u.Fragment = ""
	if u.Scheme != "https" || u.User != nil || !strings.EqualFold(u.Hostname(), base.Hostname()) || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" {
		return "", ErrInventory
	}
	valid := ninthEmployerJobs.MatchString(u.EscapedPath())
	if o.Variant == "pandape" {
		valid = ninthPandapeJobs.MatchString(u.EscapedPath())
	}
	if !valid {
		return "", ErrInventory
	}
	u.Host = strings.ToLower(u.Hostname())
	return u.String(), nil
}

func ParseComputrabajoListing(body []byte, o NinthProviderOptions, page int) ([]string, int, error) {
	if o.Provider != "computrabajo" || page < 1 || page > 500 {
		return nil, 0, ErrOptions
	}
	tree, err := eighthTree(body)
	if err != nil {
		return nil, 0, err
	}
	totalText, selector := "", "a.js-o-link[href]"
	if o.Variant == "pandape" {
		if eighthFirst(tree, "section#VacancySection") == nil {
			return nil, 0, ErrInventory
		}
		totalText = eighthText(eighthFirst(tree, "div.color-title.font-3xl"))
		selector = "a.card-vacancy[href]"
		if eighthAttr(eighthFirst(tree, "input#hdn_PageSize"), "value") != "20" || eighthAttr(eighthFirst(tree, "input#hdn_PageNumber"), "value") != strconv.Itoa(page) {
			return nil, 0, ErrInventory
		}
	} else {
		canonical := eighthFirst(tree, `link[rel~="canonical"]`)
		other, err := NinthProviderOptionsFromMetadata("computrabajo", eighthAttr(canonical, "href"), "{}")
		if err != nil || other.Origin != o.Origin || other.Slug != o.Slug {
			return nil, 0, ErrInventory
		}
		for _, node := range eighthSelect(tree, "meta[name]") {
			if strings.EqualFold(eighthAttr(node, "name"), "title") {
				totalText = eighthAttr(node, "content")
				break
			}
		}
	}
	pattern := ninthEmployerCount
	if o.Variant == "pandape" {
		pattern = ninthPandapeCount
	}
	match := pattern.FindStringSubmatch(totalText)
	if match == nil {
		return nil, 0, ErrInventory
	}
	digits := strings.NewReplacer(".", "", ",", "").Replace(match[1])
	total, err := strconv.Atoi(digits)
	if err != nil || total < 0 {
		return nil, 0, ErrInventory
	}
	if o.Variant == "pandape" && strings.ToLower(eighthAttr(eighthFirst(tree, "input#hdn_isLast"), "value")) != strconv.FormatBool(page*20 >= total) {
		return nil, 0, ErrInventory
	}
	out, seen := []string{}, map[string]bool{}
	for _, link := range eighthSelect(tree, selector) {
		source, err := ComputrabajoJobURL(eighthAttr(link, "href"), o)
		if err != nil || seen[source] {
			return nil, 0, ErrInventory
		}
		seen[source] = true
		out = append(out, source)
	}
	expected := min(20, max(0, total-(page-1)*20))
	if len(out) != expected {
		return nil, 0, ErrInventory
	}
	sort.Strings(out)
	return out, total, nil
}
