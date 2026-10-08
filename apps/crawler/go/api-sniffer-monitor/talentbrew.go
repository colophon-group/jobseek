package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"unicode"

	"golang.org/x/net/html"
)

type TalentBrewOptions struct {
	BoardURL                      string
	MaxPages, PageChars, AjaxSize int
	Ajax                          bool
}
type TalentBrewPage struct {
	URLs                           []string
	Total, Pages, Current, PerPage *int
	AjaxURL                        string
	Attributes                     map[string]string
}
type TalentBrewFetch func(context.Context, string, int) (string, bool, error)

func TalentBrewOptionsFromMetadata(boardURL, metadata string) (TalentBrewOptions, error) {
	o := TalentBrewOptions{BoardURL: boardURL, MaxPages: 5000, PageChars: 5000000, AjaxSize: 1000, Ajax: true}
	if !validURL(boardURL) {
		return o, ErrOptions
	}
	d, err := Decode([]byte(metadata))
	if err != nil {
		return o, err
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	for k, v := range m {
		switch k {
		case "max_pages", "page_max_chars", "ajax_page_size", "page_size", "records_per_page":
			if v == nil {
				continue
			}
			n, ok := integer(v)
			if !ok || n < 1 {
				return o, ErrOptions
			}
			if k == "max_pages" {
				o.MaxPages = min(n, 5000)
			} else if k == "page_max_chars" {
				o.PageChars = min(n, 25000000)
			}
		case "ajax":
			b, ok := v.(bool)
			if !ok {
				return o, ErrOptions
			}
			o.Ajax = b
		case "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor":
		default:
			return o, ErrOptions
		}
	}
	for _, key := range []string{"ajax_page_size", "page_size", "records_per_page"} {
		if v := m[key]; v != nil {
			n, ok := integer(v)
			if !ok || n < 1 {
				return o, ErrOptions
			}
			o.AjaxSize = min(n, 10000)
			break
		}
	}
	return o, nil
}

func ParseTalentBrewPage(body, base string) (TalentBrewPage, error) {
	p := TalentBrewPage{URLs: []string{}, Attributes: map[string]string{}}
	tree, err := html.ParseWithOptions(strings.NewReader(body), html.ParseOptionEnableScripting(false))
	if err != nil {
		return p, err
	}
	links, fallback := map[string]bool{}, map[string]bool{}
	sawList := false
	badCounter := false
	var visit func(*html.Node, bool)
	visit = func(node *html.Node, inside bool) {
		attrs := map[string]string{}
		for _, a := range node.Attr {
			attrs[a.Key] = a.Val
		}
		if attrs["id"] == "search-results" {
			p.Attributes = attrs
			count := func(keys ...string) *int {
				for i, key := range keys {
					n, ok, overflow := talentBrewInteger(attrs[key])
					badCounter = badCounter || overflow
					if ok && (n != 0 || i == len(keys)-1) {
						return &n
					}
				}
				return nil
			}
			p.Total = count("data-total-job-results", "data-total-results")
			p.Pages = count("data-total-pages")
			p.Current = count("data-current-page")
			p.PerPage = count("data-records-per-page")
			p.AjaxURL = attrs["data-ajax-url"]
		}
		if attrs["id"] == "search-results-list" {
			inside = true
			sawList = true
		}
		if node.Type == html.ElementNode && node.Data == "a" && attrs["href"] != "" {
			raw, e := PythonJoinURL(base, attrs["href"])
			parsed, ok := ParsePythonURL(raw)
			if e == nil && ok && strings.HasPrefix(raw, "http") && strings.Contains(strings.ToLower(parsed.Path), "/job/") {
				if inside {
					links[raw] = true
				} else if attrs["data-job-id"] != "" {
					fallback[raw] = true
				}
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			visit(child, inside)
		}
	}
	visit(tree, false)
	if badCounter {
		return p, ErrInventory
	}
	if !sawList {
		links = fallback
	}
	for raw := range links {
		p.URLs = append(p.URLs, raw)
	}
	sort.Strings(p.URLs)
	return p, nil
}

func (o TalentBrewOptions) PageURL(page int) string {
	if page <= 1 {
		return o.BoardURL
	}
	u, _ := url.Parse(o.BoardURL)
	q := u.Query()
	q.Set("p", strconv.Itoa(page))
	u.RawQuery = q.Encode()
	return u.String()
}
func (o TalentBrewOptions) ResourceMatches(raw string) bool {
	a, e1 := url.Parse(o.BoardURL)
	b, e2 := url.Parse(raw)
	if e1 != nil || e2 != nil || b.Scheme != a.Scheme || b.Host != a.Host || b.User != nil || b.Fragment != "" {
		return false
	}
	prefix := strings.TrimSuffix(a.Path, "/search-jobs")
	return b.Path == a.Path || b.Path == a.Path+"/results" || b.Path == prefix+"/search-results" || b.Path == "/search-jobs/results" || b.Path == "/search-results"
}
func (o TalentBrewOptions) AjaxURL(p TalentBrewPage, page int) (string, error) {
	raw, err := PythonJoinURL(o.BoardURL, p.AjaxURL)
	if err != nil || !o.ResourceMatches(raw) {
		return "", ErrOptions
	}
	u, _ := url.Parse(raw)
	q := u.Query()
	defaults := []struct{ key, attr, value string }{{"ActiveFacetID", "data-active-facet-id", "0"}, {"Distance", "data-distance", "50"}, {"ShowRadius", "data-show-radius", "False"}, {"CustomFacetName", "data-custom-facet-name", ""}, {"FacetTerm", "data-facet-term", ""}, {"FacetType", "data-facet-type", "0"}, {"SearchResultsModuleName", "data-search-results-module-name", "Search Results"}, {"SearchFiltersModuleName", "data-search-filters-module-name", "Search Filters"}, {"SortCriteria", "data-sort-criteria", "0"}, {"SortDirection", "data-sort-direction", "0"}, {"SearchType", "data-search-type", "5"}, {"PostalCode", "data-postal-code", ""}}
	for _, d := range defaults {
		value := p.Attributes[d.attr]
		if value == "" {
			value = d.value
		}
		q.Add(d.key, value)
	}
	q.Add("CurrentPage", strconv.Itoa(page))
	q.Add("RecordsPerPage", strconv.Itoa(o.AjaxSize))
	for _, d := range []struct{ key, attr string }{{"Keywords", "data-keywords"}, {"Location", "data-location"}, {"Latitude", "data-latitude"}, {"Longitude", "data-longitude"}, {"KeywordType", "data-keyword-type"}, {"LocationType", "data-location-type"}, {"LocationPath", "data-location-path"}, {"OrganizationIds", "data-organization-ids"}} {
		if v := p.Attributes[d.attr]; v != "" {
			q.Add(d.key, v)
		}
	}
	board, _ := url.Parse(o.BoardURL)
	index := 0
	for _, v := range board.Query()["fl"] {
		for _, id := range strings.Split(v, ",") {
			if id = strings.TrimSpace(id); id != "" {
				prefix := "FacetFilters[" + strconv.Itoa(index) + "]."
				q.Add(prefix+"ID", id)
				q.Add(prefix+"FacetType", "3")
				q.Add(prefix+"IsApplied", "true")
				index++
			}
		}
	}
	u.RawQuery = q.Encode()
	return u.String(), nil
}

func DiscoverTalentBrew(ctx context.Context, o TalentBrewOptions, fetch TalentBrewFetch) ([]string, error) {
	if fetch == nil {
		return nil, ErrOptions
	}
	body, present, err := fetch(ctx, o.BoardURL, o.PageChars)
	if err != nil {
		return nil, err
	}
	if !present {
		return []string{}, nil
	}
	first, err := ParseTalentBrewPage(body, o.BoardURL)
	if err != nil {
		return nil, err
	}
	urls := map[string]bool{}
	add := func(rows []string) bool {
		before := len(urls)
		for _, raw := range rows {
			urls[raw] = true
		}
		return len(urls) > before
	}
	finish := func() ([]string, error) {
		out := []string{}
		for raw := range urls {
			out = append(out, raw)
		}
		sort.Strings(out)
		if len(out) > 50000 {
			out = out[:50000]
		}
		if first.Total != nil && len(out) < *first.Total {
			return nil, ErrInventory
		}
		return out, nil
	}

	if o.Ajax && first.AjaxURL != "" {
		pages := 1
		if first.Total != nil {
			pages = (*first.Total + o.AjaxSize - 1) / o.AjaxSize
		} else if first.Pages != nil {
			pages = *first.Pages
		}
		pages = min(max(1, pages), o.MaxPages)
		fallback := false
		for page := 1; page <= pages; page++ {
			endpoint, err := o.AjaxURL(first, page)
			if err != nil {
				return nil, err
			}
			body, present, err = fetch(ctx, endpoint, max(5000000, min(o.AjaxSize*5000, 25000000)))
			if err != nil {
				return nil, err
			}
			if !present {
				break
			}
			var payload any
			if json.Unmarshal([]byte(body), &payload) != nil {
				fallback = true
				break
			}
			object, ok := payload.(map[string]any)
			var results any
			if ok {
				results = object["results"]
			}
			if !detailTruthy(results) {
				fallback = page == 1
				break
			}
			markup, ok := results.(string)
			if !ok {
				return nil, ErrInventory
			}
			parsed, err := ParseTalentBrewPage(markup, o.BoardURL)
			if err != nil {
				return nil, err
			}
			if len(parsed.URLs) == 0 {
				fallback = page == 1
				break
			}
			if !add(parsed.URLs) {
				break
			}
			if first.Total != nil && len(urls) >= *first.Total || len(urls) >= 50000 {
				break
			}
		}
		if !fallback {
			return finish()
		}
		urls = map[string]bool{}
	}
	add(first.URLs)
	pages := 1
	if first.Pages != nil {
		pages = *first.Pages
	} else if first.Total != nil && first.PerPage != nil && *first.PerPage != 0 {
		pages = (*first.Total + *first.PerPage - 1) / *first.PerPage
	}
	pages = min(max(1, pages), o.MaxPages)
	for page := 2; page <= pages; page++ {
		endpoint := o.PageURL(page)
		body, present, err = fetch(ctx, endpoint, o.PageChars)
		if err != nil {
			return nil, err
		}
		if !present {
			break
		}
		parsed, err := ParseTalentBrewPage(body, endpoint)
		if err != nil {
			return nil, err
		}
		if !add(parsed.URLs) || len(urls) >= 50000 {
			break
		}
	}
	return finish()
}

// Python int accepts decimal Unicode digits and single underscores between digits.
func talentBrewInteger(raw string) (int, bool, bool) {
	text := strings.TrimFunc(raw, func(r rune) bool { return unicode.IsSpace(r) && !(r >= 0x1c && r <= 0x1f) })
	out := strings.Builder{}
	previousDigit := false
	for i, r := range text {
		if i == 0 && (r == '+' || r == '-') {
			out.WriteRune(r)
			continue
		}
		if r == '_' {
			if !previousDigit {
				return 0, false, false
			}
			previousDigit = false
			continue
		}
		digit := -1
		for _, a := range unicode.Nd.R16 {
			if r >= rune(a.Lo) && r <= rune(a.Hi) && (r-rune(a.Lo))%rune(a.Stride) == 0 {
				digit = int((r - rune(a.Lo)) / rune(a.Stride) % 10)
				break
			}
		}
		if digit < 0 {
			for _, a := range unicode.Nd.R32 {
				if r >= rune(a.Lo) && r <= rune(a.Hi) && (r-rune(a.Lo))%rune(a.Stride) == 0 {
					digit = int((r - rune(a.Lo)) / rune(a.Stride) % 10)
					break
				}
			}
		}
		if digit < 0 {
			return 0, false, false
		}
		out.WriteByte(byte(digit) + '0')
		previousDigit = true
	}
	if !previousDigit {
		return 0, false, false
	}
	n, err := strconv.Atoi(out.String())
	return n, err == nil, err != nil
}
