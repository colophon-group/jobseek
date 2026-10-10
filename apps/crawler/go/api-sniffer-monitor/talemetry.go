package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

// TalemetryOptions retains the original HTML/JSON transports and bounded
// snapshot retry. Transport and publication authority remain with the worker.
type TalemetryOptions struct {
	BoardURL, Origin       string
	JSON, Proxy            bool
	MaxPages, PageMaxChars int
}

func TalemetryOptionsFromMetadata(board, metadata string) (TalemetryOptions, error) {
	o := TalemetryOptions{BoardURL: board, MaxPages: 5000, PageMaxChars: 5_000_000}
	u, err := url.Parse(board)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Hostname() == "" || u.Opaque != "" || u.RawPath != "" || u.Port() != "" && u.Port() != "443" || u.Path != "/search/jobs" {
		return o, ErrOptions
	}
	o.Origin = "https://" + strings.ToLower(u.Host)
	m, err := DecodeInlineMetadata(metadata)
	if err != nil {
		return o, err
	}
	localizedServiceAnnotations(m)
	for k, v := range m {
		switch k {
		case "proxy":
			b, ok := v.(bool)
			if !ok {
				return o, ErrOptions
			}
			o.Proxy = b
		case "transport":
			if v != "jobs_json" {
				return o, ErrOptions
			}
			o.JSON = true
		case "max_pages", "page_max_chars":
			if v == nil {
				continue
			}
			n, e := talemetryOptionInteger(v)
			if e != nil {
				return o, ErrOptions
			}
			if k == "max_pages" {
				o.MaxPages = min(n, 5000)
			} else {
				o.PageMaxChars = max(1, min(n, 25_000_000))
			}
		case "rescrape_policy", "delist_threshold": // Existing scheduler fields.
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}

// Queue inspectors validate these shared immutable/runtime fields. Parsers
// consume only provider options; this never changes the ownership hash input.
func localizedServiceAnnotations(m map[string]any) {
	for _, k := range []string{"token", "board_token", "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "_confirmed_drop_candidate", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
		delete(m, k)
	}
}

func talemetryOptionInteger(v any) (int, error) {
	var f float64
	switch n := v.(type) {
	case string:
		return strconv.Atoi(strings.TrimFunc(n, enterpriseSpace))
	case json.Number:
		var err error
		f, err = strconv.ParseFloat(n.String(), 64)
		if err != nil {
			return 0, ErrOptions
		}
	case float64:
		f = n
	case bool:
		if n {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, ErrOptions
	}
	if math.IsNaN(f) || math.IsInf(f, 0) || f >= float64(math.MaxInt) || f < float64(math.MinInt) {
		return 0, ErrOptions
	}
	return int(f), nil
}

func (o TalemetryOptions) Profile() string {
	if o.JSON {
		if o.Proxy {
			return "talemetry.proxy-json-urls/v1"
		}
		return "talemetry.json-urls/v1"
	}
	if o.Proxy {
		return "talemetry.proxy-listing-urls/v1"
	}
	return "talemetry.listing-urls/v1"
}

func (o TalemetryOptions) PageURL(page int) string {
	if page <= 1 && !o.JSON {
		return o.BoardURL
	}
	u, _ := url.Parse(o.BoardURL)
	pairs := [][2]string{}
	// Python parse_qsl preserves order and repeated non-page parameters.
	for _, part := range strings.Split(u.RawQuery, "&") {
		if part == "" {
			continue
		}
		k, v, _ := strings.Cut(part, "=")
		key, e1 := url.QueryUnescape(k)
		val, e2 := url.QueryUnescape(v)
		if e1 == nil && e2 == nil && key != "page" {
			pairs = append(pairs, [2]string{key, val})
		}
	}
	pairs = append(pairs, [2]string{"page", strconv.Itoa(page)})
	u.RawQuery = lastQuery(pairs)
	u.Fragment = ""
	u.RawFragment = ""
	if o.JSON && !strings.HasSuffix(strings.TrimRight(u.Path, "/"), ".json") {
		u.Path = strings.TrimRight(u.Path, "/") + ".json"
	}
	return u.String()
}

func (o TalemetryOptions) ResourceMatches(raw string) bool {
	u, e := url.Parse(raw)
	b, _ := url.Parse(o.BoardURL)
	if e != nil || u.Scheme != "https" || u.User != nil || !strings.EqualFold(u.Host, b.Host) || u.Fragment != "" || u.RawPath != "" {
		return false
	}
	path := b.Path
	if o.JSON {
		path = strings.TrimRight(path, "/") + ".json"
	}
	if u.Path != path {
		return false
	}
	if !o.JSON && raw == o.BoardURL {
		return true
	}
	values := u.Query()["page"]
	if len(values) != 1 {
		return false
	}
	page, e := strconv.Atoi(values[0])
	return e == nil && page >= 1 && page <= o.MaxPages && raw == o.PageURL(page)
}

type TalemetryPage struct {
	URLs, IDs               []string
	Start, End, Total, Size int
	Marked, Listed, Counted bool
}

var talemetryRange = regexp.MustCompile(`(?i)\b(?:Showing|Viewing)\s+([0-9,]+)\s*-\s*([0-9,]+)\s+of\s+([0-9,]+)\s+results\b`)
var talemetryJobPath = regexp.MustCompile(`(?i)^/jobs/[0-9]+(?:-[^/?#]+)?/?$`)
var talemetryPermalink = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._~-]*$`)
var errTalemetrySnapshot = errors.New("Talemetry inventory changed during snapshot")

func ParseTalemetryHTML(source, base string) (TalemetryPage, error) {
	p := TalemetryPage{}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return p, e
	}
	text := []string{}
	var walk func(*xhtml.Node)
	walk = func(n *xhtml.Node) {
		if n.Type == xhtml.TextNode {
			text = append(text, n.Data)
			s := strings.ToLower(n.Data)
			p.Marked = p.Marked || strings.Contains(s, "window.talemetry") || strings.Contains(s, "talemetry_careersites")
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(doc)
	m := talemetryRange.FindStringSubmatch(strings.Join(text, " "))
	if len(m) == 4 {
		p.Counted = true
		dest := []*int{&p.Start, &p.End, &p.Total}
		for i, d := range dest {
			n, e := strconv.Atoi(strings.ReplaceAll(m[i+1], ",", ""))
			if e != nil {
				return p, ErrInventory
			}
			*d = n
		}
	}
	seen := map[string]bool{}
	b, e := url.Parse(base)
	if e != nil {
		return p, ErrOptions
	}
	for _, list := range cascadia.QueryAll(doc, cascadia.MustCompile(".jobs-section__list")) {
		p.Listed = true
		for _, link := range cascadia.QueryAll(list, cascadia.MustCompile("a[href]")) {
			href, _ := lastNodeAttribute(link, "href")
			r, e := url.Parse(href)
			if e != nil || href == "" {
				continue
			}
			u := b.ResolveReference(r)
			if u.Scheme == b.Scheme && strings.EqualFold(u.Host, b.Host) && talemetryJobPath.MatchString(u.Path) {
				u.RawQuery = ""
				u.ForceQuery = false
				u.Fragment = ""
				u.RawFragment = ""
				seen[u.String()] = true
			}
		}
	}
	for u := range seen {
		p.URLs = append(p.URLs, u)
	}
	sort.Strings(p.URLs)
	return p, nil
}

func talemetryJSONInteger(v any, minimum int) (int, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	i, e := strconv.Atoi(n.String())
	return i, e == nil && i >= minimum
}
func talemetryJSONID(v any) (string, bool) {
	var s string
	switch n := v.(type) {
	case string:
		s = n
	case json.Number:
		if _, e := strconv.Atoi(n.String()); e != nil {
			return "", false
		}
		s = n.String()
	default:
		return "", false
	}
	if !smallUnsigned.MatchString(s) {
		return "", false
	}
	trim := strings.TrimLeft(s, "0")
	return s, trim != ""
}

func ParseTalemetryJSON(body []byte, base string, page int, expected *TalemetryPage) (TalemetryPage, error) {
	p := TalemetryPage{Marked: true, Listed: true, Counted: true}
	d, e := Decode(body)
	if e != nil {
		return p, e
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return p, ErrInventory
	}
	var valid bool
	p.Total, valid = talemetryJSONInteger(m["total_entries"], 0)
	if !valid {
		return p, ErrInventory
	}
	p.Size, valid = talemetryJSONInteger(m["per_page"], 1)
	if !valid {
		return p, ErrInventory
	}
	current, valid := talemetryJSONInteger(m["current_page"], 1)
	if !valid {
		return p, ErrInventory
	}
	entries, ok := m["entries"].([]any)
	if !ok {
		return p, ErrInventory
	}
	if current != page || expected != nil && (p.Total != expected.Total || p.Size != expected.Size) || len(entries) != max(0, min(p.Size, p.Total-(page-1)*p.Size)) {
		return p, errTalemetrySnapshot
	}
	b, e := url.Parse(base)
	if e != nil {
		return p, ErrOptions
	}
	ids, urls := map[string]bool{}, map[string]bool{}
	for _, raw := range entries {
		row, ok := raw.(map[string]any)
		if !ok {
			return p, ErrInventory
		}
		id, ok := talemetryJSONID(row["id"])
		if !ok {
			return p, ErrInventory
		}
		tid, ok := talemetryJSONID(row["talemetry_job_id"])
		if !ok {
			return p, ErrInventory
		}
		if id != tid || ids[id] {
			return p, errTalemetrySnapshot
		}
		slug, ok := row["permalink"].(string)
		if !ok || !talemetryPermalink.MatchString(slug) {
			return p, ErrInventory
		}
		u := *b
		u.Path = "/jobs/" + id + "-" + slug
		u.RawQuery = ""
		u.ForceQuery = false
		u.Fragment = ""
		u.RawFragment = ""
		ids[id] = true
		urls[u.String()] = true
	}
	if len(urls) != len(entries) {
		return p, errTalemetrySnapshot
	}
	for id := range ids {
		p.IDs = append(p.IDs, id)
	}
	for u := range urls {
		p.URLs = append(p.URLs, u)
	}
	sort.Strings(p.IDs)
	sort.Strings(p.URLs)
	return p, nil
}

func validateTalemetryHTML(p TalemetryPage, page int, expected *TalemetryPage) error {
	if !p.Marked || !p.Listed || !p.Counted {
		return ErrInventory
	}
	if p.Total == 0 {
		if page != 1 || p.Start != 0 || p.End != 0 || len(p.URLs) != 0 {
			return ErrInventory
		}
		return nil
	}
	if p.Start < 1 || p.End < p.Start || p.End > p.Total || len(p.URLs) != p.End-p.Start+1 {
		return ErrInventory
	}
	if expected != nil && (p.Total != expected.Total || p.Start != (page-1)*expected.End+1 || p.End != min(page*expected.End, expected.Total)) {
		return errTalemetrySnapshot
	}
	return nil
}

func talemetryOnce(ctx context.Context, o TalemetryOptions, fetch SmallProviderFetch) ([]string, error) {
	headers := http.Header{}
	if o.JSON {
		for k, v := range map[string]string{"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": "https://parkercareers.ttcportals.com/jobs/search", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"} {
			headers.Set(k, v)
		}
	}
	get := func(page int, expected *TalemetryPage) (TalemetryPage, error) {
		body, e := fetch(ctx, Request{Method: "GET", URL: o.PageURL(page), Headers: headers.Clone()})
		if e != nil {
			return TalemetryPage{}, e
		}
		if o.JSON {
			return ParseTalemetryJSON(body, o.BoardURL, page, expected)
		}
		source := string(body)
		if runes := []rune(source); len(runes) > o.PageMaxChars {
			source = string(runes[:o.PageMaxChars])
		}
		p, e := ParseTalemetryHTML(source, o.PageURL(page))
		if e == nil {
			e = validateTalemetryHTML(p, page, expected)
		}
		return p, e
	}
	first, e := get(1, nil)
	if e != nil {
		return nil, e
	}
	if first.Total == 0 {
		return []string{}, nil
	}
	if first.Total > 50_000 {
		return nil, ErrInventory
	}
	size := first.End
	if o.JSON {
		size = first.Size
	}
	if size < 1 {
		return nil, ErrInventory
	}
	pages := 1 + (first.Total-1)/size
	if pages > o.MaxPages {
		return nil, ErrInventory
	}
	urls, ids := map[string]bool{}, map[string]bool{}
	for _, u := range first.URLs {
		urls[u] = true
	}
	for _, id := range first.IDs {
		ids[id] = true
	}
	for page := 2; page <= pages; page++ {
		p, e := get(page, &first)
		if e != nil {
			return nil, e
		}
		for _, u := range p.URLs {
			if urls[u] {
				return nil, errTalemetrySnapshot
			}
			urls[u] = true
		}
		for _, id := range p.IDs {
			if ids[id] {
				return nil, errTalemetrySnapshot
			}
			ids[id] = true
		}
	}
	if len(urls) != first.Total || o.JSON && len(ids) != first.Total {
		return nil, errTalemetrySnapshot
	}
	out := []string{}
	for u := range urls {
		out = append(out, u)
	}
	sort.Strings(out)
	return out, nil
}

func DiscoverTalemetry(ctx context.Context, o TalemetryOptions, fetch SmallProviderFetch, wait func(context.Context, time.Duration) error) ([]string, error) {
	if fetch == nil || wait == nil {
		return nil, ErrOptions
	}
	for attempt := 0; attempt < 2; attempt++ {
		urls, e := talemetryOnce(ctx, o, fetch)
		if e == nil || !errors.Is(e, errTalemetrySnapshot) || attempt == 1 {
			return urls, e
		}
		if e = wait(ctx, time.Second); e != nil {
			return nil, e
		}
	}
	return nil, ErrInventory
}
