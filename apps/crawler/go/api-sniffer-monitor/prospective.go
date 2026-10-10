package apisniffer

import (
	"context"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
	xhtml "golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type ProspectiveOptions struct {
	BoardURL, Origin, Medium        string
	Filters                         [][2]string
	LinkTexts                       map[string]bool
	SourcePattern, CanonicalPattern *regexp2.Regexp
	Locales                         []string
	Concurrency                     int
	Aliases                         map[string]string
}
type ProspectivePage struct {
	URLs            []string
	Next            int
	Limit, Language string
}
type ProspectiveDetailParser func(string, []byte) (map[string]any, error)

var prospectiveUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)
var prospectiveJobPath = regexp.MustCompile(`(?i)^/offene-stellen/[^/]+/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/?$`)
var prospectiveMedium = regexp.MustCompile(`/careercenter/([0-9]+)/assets/`)
var prospectivePagination = regexp.MustCompile(`\bsendPagination\(([0-9]+)\)`)
var prospectiveFilter = regexp.MustCompile(`^filter_[0-9]+$`)

func ProspectiveOptionsFromMetadata(board, metadata string) (ProspectiveOptions, error) {
	o := ProspectiveOptions{BoardURL: board, LinkTexts: map[string]bool{}, Aliases: map[string]string{}, Concurrency: 8}
	u, e := url.Parse(board)
	if e != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Opaque != "" || u.RawPath != "" || u.Port() != "" && u.Port() != "443" || u.Fragment != "" {
		return o, ErrOptions
	}
	o.Origin = "https://" + u.Host
	m, e := DecodeInlineMetadata(metadata)
	if e != nil {
		return o, e
	}
	localizedServiceAnnotations(m)
	for k := range m {
		if k != "medium_id" && k != "filters" && k != "application_identity" && k != "rescrape_policy" && k != "delist_threshold" {
			return o, ErrOptions
		}
	}
	if v := m["medium_id"]; v != nil {
		s, ok := v.(string)
		if !ok || !smallUnsigned.MatchString(s) {
			return o, ErrOptions
		}
		o.Medium = s
	}
	if v := m["filters"]; v != nil {
		filters, ok := v.(map[string]any)
		if !ok || len(filters) > 8 {
			return o, ErrOptions
		}
		d, e := Decode([]byte(metadata))
		if e != nil {
			return o, e
		}
		raw, _ := d.Value.(map[string]any)
		ordered, _ := raw["filters"].(map[string]any)
		for _, name := range d.order[reflect.ValueOf(ordered)] {
			v := filters[name]
			if !prospectiveFilter.MatchString(name) {
				return o, ErrOptions
			}
			values, ok := v.([]any)
			if s, yes := v.(string); yes {
				values = []any{s}
				ok = true
			}
			if !ok || len(values) < 1 || len(values) > 100 {
				return o, ErrOptions
			}
			for _, value := range values {
				s, ok := value.(string)
				if !ok || s == "" || len([]rune(s)) > 128 || strings.ContainsRune(s, 0) {
					return o, ErrOptions
				}
				o.Filters = append(o.Filters, [2]string{name, s})
			}
		}
	}
	identity, ok := m["application_identity"].(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	for k := range identity {
		if k != "link_texts" && k != "source_url_allowlist" && k != "canonical_url_allowlist" && k != "locale_priority" && k != "concurrency" && k != "source_url_aliases" {
			return o, ErrOptions
		}
	}
	texts, ok := identity["link_texts"].([]any)
	if !ok || len(texts) < 1 || len(texts) > 8 {
		return o, ErrOptions
	}
	for _, v := range texts {
		s, ok := v.(string)
		if !ok || len([]rune(s)) > 64 || strings.TrimFunc(s, enterpriseSpace) == "" {
			return o, ErrOptions
		}
		s = cases.Fold().String(strings.Join(strings.FieldsFunc(s, enterpriseSpace), " "))
		if o.LinkTexts[s] {
			return o, ErrOptions
		}
		o.LinkTexts[s] = true
	}
	for key, dest := range map[string]**regexp2.Regexp{"source_url_allowlist": &o.SourcePattern, "canonical_url_allowlist": &o.CanonicalPattern} {
		s, ok := identity[key].(string)
		if !ok || s == "" || len([]rune(s)) > 4096 {
			return o, ErrOptions
		}
		*dest, e = dom.CompileURLPattern(`\A(?:` + s + `)\z`)
		if e != nil {
			return o, ErrOptions
		}
	}
	locales, ok := identity["locale_priority"].([]any)
	if !ok || len(locales) < 1 || len(locales) > 8 {
		return o, ErrOptions
	}
	seen := map[string]bool{}
	for _, v := range locales {
		s, ok := v.(string)
		if !ok || !regexp.MustCompile(`^[a-z]{2}$`).MatchString(s) || seen[s] {
			return o, ErrOptions
		}
		seen[s] = true
		o.Locales = append(o.Locales, s)
	}
	if v, exists := identity["concurrency"]; exists {
		n, ok := talemetryJSONInteger(v, 1)
		if !ok || n > 16 {
			return o, ErrOptions
		}
		o.Concurrency = n
	}
	if v, exists := identity["source_url_aliases"]; exists {
		aliases, ok := v.(map[string]any)
		if !ok || len(aliases) > 128 {
			return o, ErrOptions
		}
		for k, v := range aliases {
			s, ok := v.(string)
			key := strings.ToLower(k)
			if !ok || !prospectiveUUID.MatchString(k) || !prospectiveUUID.MatchString(s) || o.Aliases[key] != "" {
				return o, ErrOptions
			}
			o.Aliases[key] = strings.ToLower(s)
		}
		for _, id := range o.Aliases {
			if o.Aliases[id] != id {
				return o, ErrOptions
			}
		}
	}
	return o, nil
}

func prospectiveMatches(pattern *regexp2.Regexp, s string) bool {
	if pattern == nil {
		return false
	}
	ok, e := pattern.MatchString(s)
	return e == nil && ok
}
func (o ProspectiveOptions) ResourceMatches(raw string) bool {
	if raw == o.BoardURL {
		return true
	}
	b, _ := url.Parse(o.BoardURL)
	u, e := url.Parse(raw)
	if e == nil && u.User == nil && u.Scheme == b.Scheme && strings.EqualFold(u.Host, b.Host) && u.RawQuery == "" && u.Fragment == "" && prospectiveJobPath.MatchString(u.Path) {
		return true
	}
	return prospectiveMatches(o.SourcePattern, raw) || prospectiveMatches(o.CanonicalPattern, raw)
}

func (o ProspectiveOptions) ApplicationResource(raw string) bool {
	return raw != o.BoardURL && (prospectiveMatches(o.SourcePattern, raw) || prospectiveMatches(o.CanonicalPattern, raw))
}
func (o ProspectiveOptions) SourceAlias(raw string) string {
	u, e := url.Parse(raw)
	if e != nil {
		return ""
	}
	m := prospectiveJobPath.FindStringSubmatch(u.Path)
	if len(m) != 2 {
		return ""
	}
	id := o.Aliases[strings.ToLower(m[1])]
	if id == "" {
		return ""
	}
	u.Path = "/offene-stellen/job/" + id
	u.RawQuery = ""
	u.Fragment = ""
	u.RawFragment = ""
	return u.String()
}

func ParseProspectivePage(source string, o ProspectiveOptions) (ProspectivePage, error) {
	p := ProspectivePage{Next: -1}
	medium := prospectiveMedium.FindStringSubmatch(source)
	if len(medium) != 2 || o.Medium != "" && medium[1] != o.Medium {
		return p, ErrInventory
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return p, e
	}
	form := cascadia.Query(doc, cascadia.MustCompile("form#careercenter-form"))
	if form == nil {
		return p, ErrInventory
	}
	method, _ := lastNodeAttribute(form, "method")
	if !strings.EqualFold(method, "post") {
		return p, ErrInventory
	}
	filters := map[string][]string{}
	for _, kv := range o.Filters {
		filters[kv[0]] = append(filters[kv[0]], kv[1])
	}
	for name, selected := range filters {
		nodes := cascadia.QueryAll(doc, cascadia.MustCompile(`select[name="`+name+`"]`))
		if len(nodes) != 1 {
			return p, ErrInventory
		}
		available := map[string]bool{}
		for _, n := range cascadia.QueryAll(nodes[0], cascadia.MustCompile("option[value]")) {
			v, _ := lastNodeAttribute(n, "value")
			available[v] = true
		}
		for _, v := range selected {
			if !available[v] {
				return p, ErrInventory
			}
		}
	}
	lists := cascadia.QueryAll(doc, cascadia.MustCompile("#jobs-list"))
	if len(lists) != 1 {
		return p, ErrInventory
	}
	b, _ := url.Parse(o.BoardURL)
	seen := map[string]bool{}
	for _, n := range cascadia.QueryAll(lists[0], cascadia.MustCompile("a.job-title[href]")) {
		href, _ := lastNodeAttribute(n, "href")
		r, e := url.Parse(href)
		if e != nil {
			return p, ErrInventory
		}
		u := b.ResolveReference(r)
		m := prospectiveJobPath.FindStringSubmatch(u.Path)
		if len(m) != 2 || u.Scheme != b.Scheme || !strings.EqualFold(u.Host, b.Host) || u.RawQuery != "" || u.Fragment != "" {
			return p, ErrInventory
		}
		seen[b.Scheme+"://"+b.Host+"/offene-stellen/job/"+strings.ToLower(m[1])] = true
	}
	if len(seen) == 0 {
		empty := cascadia.QueryAll(lists[0], cascadia.MustCompile("#no-results"))
		if len(empty) != 1 || strings.TrimFunc(paylocityText(empty[0], ""), enterpriseSpace) == "" {
			return p, ErrInventory
		}
	}
	single := func(name, def string) (string, error) {
		nodes := cascadia.QueryAll(doc, cascadia.MustCompile(`input[name="`+name+`"]`))
		if len(nodes) > 1 {
			return "", ErrInventory
		}
		if len(nodes) == 0 {
			return def, nil
		}
		s, ok := lastNodeAttribute(nodes[0], "value")
		if !ok {
			return def, nil
		}
		return s, nil
	}
	offset, e := single("offset", "0")
	if e != nil {
		return p, e
	}
	if active := cascadia.Query(doc, cascadia.MustCompile("#pagination .page.active[onclick]")); active != nil {
		action, _ := lastNodeAttribute(active, "onclick")
		m := prospectivePagination.FindStringSubmatch(action)
		if len(m) != 2 {
			return p, ErrInventory
		}
		offset = m[1]
	}
	p.Limit, e = single("limit", "10")
	if e != nil {
		return p, e
	}
	p.Language, e = single("lang", "de")
	if e != nil {
		return p, e
	}
	current, e := strconv.Atoi(offset)
	limit, e2 := strconv.Atoi(p.Limit)
	if e != nil || e2 != nil || !smallUnsigned.MatchString(offset) || !smallUnsigned.MatchString(p.Limit) || limit < 1 {
		return p, ErrInventory
	}
	for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile("#pagination [onclick]")) {
		action, _ := lastNodeAttribute(n, "onclick")
		m := prospectivePagination.FindStringSubmatch(action)
		if len(m) != 2 {
			continue
		}
		next, e := strconv.Atoi(m[1])
		if e != nil {
			return p, ErrInventory
		}
		if next > current && (p.Next < 0 || next < p.Next) {
			p.Next = next
		}
	}
	for u := range seen {
		p.URLs = append(p.URLs, u)
	}
	sort.Strings(p.URLs)
	return p, nil
}

type prospectiveVariant struct {
	URL, Locale, Source string
	Fields              map[string]any
}

func prospectiveRichJob(ctx context.Context, o ProspectiveOptions, raw string, fetch SuccessFactorsLegacyFetch, parse ProspectiveDetailParser) (prospectiveVariant, error) {
	v := prospectiveVariant{Source: raw}
	body, _, e := fetch(ctx, Request{Method: "GET", URL: raw, Headers: http.Header{"Accept": []string{"text/html,application/xhtml+xml"}}})
	if e != nil {
		return v, e
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(string(body)), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return v, e
	}
	root := cascadia.Query(doc, cascadia.MustCompile("html[lang]"))
	locale := ""
	if root != nil {
		locale, _ = lastNodeAttribute(root, "lang")
	}
	locale = strings.ToLower(strings.SplitN(locale, "-", 2)[0])
	rank := -1
	for i, s := range o.Locales {
		if s == locale {
			rank = i
		}
	}
	if rank < 0 {
		return v, ErrInventory
	}
	v.Locale = locale
	application := o.SourceAlias(raw)
	if application == "" {
		candidates := map[string]bool{}
		b, _ := url.Parse(raw)
		for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile("a[href]")) {
			text := cases.Fold().String(strings.Join(strings.FieldsFunc(paylocityText(n, " "), enterpriseSpace), " "))
			if !o.LinkTexts[text] {
				continue
			}
			href, _ := lastNodeAttribute(n, "href")
			r, e := url.Parse(href)
			if e != nil {
				return v, ErrInventory
			}
			candidates[b.ResolveReference(r).String()] = true
		}
		if len(candidates) != 1 {
			return v, ErrInventory
		}
		for u := range candidates {
			application = u
		}
		if !prospectiveMatches(o.SourcePattern, application) {
			return v, ErrInventory
		}
		visited := map[string]bool{}
		for count := 0; ; count++ {
			if visited[application] || !prospectiveMatches(o.SourcePattern, application) && !prospectiveMatches(o.CanonicalPattern, application) {
				return v, ErrInventory
			}
			visited[application] = true
			_, headers, e := fetch(ctx, Request{Method: "GET", URL: application, Headers: http.Header{"Accept": []string{"text/html,application/xhtml+xml"}}})
			if e != nil {
				return v, e
			}
			status, e := strconv.Atoi(headers.Get("X-Jobseek-Observed-Status"))
			if e != nil {
				return v, ErrInventory
			}
			if status == 301 || status == 302 || status == 303 || status == 307 || status == 308 {
				location := headers.Get("Location")
				if count >= 5 || location == "" {
					return v, ErrInventory
				}
				b, _ := url.Parse(application)
				r, e := url.Parse(location)
				if e != nil {
					return v, ErrInventory
				}
				application = b.ResolveReference(r).String()
				continue
			}
			if status < 200 || status >= 300 || !prospectiveMatches(o.CanonicalPattern, application) {
				return v, ErrInventory
			}
			break
		}
	}
	v.URL = application
	v.Fields, e = parse(raw, body)
	if e != nil {
		return v, e
	}
	if !detailTruthy(v.Fields["title"]) || !detailTruthy(v.Fields["description"]) {
		return v, ErrInventory
	}
	v.Fields["url"] = application
	v.Fields["language"] = locale
	md, _ := v.Fields["metadata"].(map[string]any)
	if md == nil {
		md = map[string]any{}
	}
	md["prospective_source_url"] = raw
	v.Fields["metadata"] = md
	return v, nil
}

func DiscoverProspective(ctx context.Context, o ProspectiveOptions, fetch SuccessFactorsLegacyFetch, parse ProspectiveDetailParser) ([]map[string]any, error) {
	if fetch == nil || parse == nil || o.Concurrency < 1 || o.Concurrency > 16 {
		return nil, ErrOptions
	}
	headers := http.Header{"Accept": []string{"text/html,application/xhtml+xml"}}
	shell, _, e := fetch(ctx, Request{Method: "GET", URL: o.BoardURL, Headers: headers})
	if e != nil {
		return nil, e
	}
	first, e := ParseProspectivePage(string(shell), o)
	if e != nil {
		return nil, e
	}
	offset := 0
	seen := map[string]bool{}
	for page := 1; page <= 200; page++ {
		pairs := [][2]string{{"offset", strconv.Itoa(offset)}, {"limit", first.Limit}, {"lang", first.Language}, {"query", ""}}
		pairs = append(pairs, o.Filters...)
		h := headers.Clone()
		h.Set("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
		h.Set("Origin", o.Origin)
		h.Set("Referer", o.BoardURL)
		body, _, e := fetch(ctx, Request{Method: "POST", URL: o.BoardURL, Body: lastQuery(pairs), Headers: h})
		if e != nil {
			return nil, e
		}
		p, e := ParseProspectivePage(string(body), o)
		if e != nil || p.Limit != first.Limit || p.Language != first.Language {
			return nil, ErrInventory
		}
		added := 0
		for _, u := range p.URLs {
			if !seen[u] {
				added++
				seen[u] = true
			}
		}
		if p.Next >= 0 && added == 0 {
			return nil, ErrInventory
		}
		if p.Next < 0 {
			break
		}
		if p.Next <= offset || page == 200 {
			return nil, ErrInventory
		}
		offset = p.Next
	}
	urls := []string{}
	for u := range seen {
		urls = append(urls, u)
	}
	sort.Strings(urls)
	variants := make([]prospectiveVariant, len(urls))
	failures := make([]error, len(urls))
	sem := make(chan struct{}, o.Concurrency)
	var wg sync.WaitGroup
	for i, u := range urls {
		wg.Add(1)
		go func(i int, u string) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				failures[i] = ctx.Err()
				return
			}
			variants[i], failures[i] = prospectiveRichJob(ctx, o, u, fetch, parse)
		}(i, u)
	}
	wg.Wait()
	for _, e := range failures {
		if e != nil {
			return nil, e
		}
	}
	grouped := map[string]map[string]prospectiveVariant{}
	for _, v := range variants {
		if grouped[v.URL] == nil {
			grouped[v.URL] = map[string]prospectiveVariant{}
		}
		if _, exists := grouped[v.URL][v.Locale]; exists {
			return nil, ErrInventory
		}
		grouped[v.URL][v.Locale] = v
	}
	keys := []string{}
	for k := range grouped {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	jobs := []map[string]any{}
	for _, key := range keys {
		group := grouped[key]
		selected := prospectiveVariant{}
		for _, locale := range o.Locales {
			if v, ok := group[locale]; ok {
				selected = v
				break
			}
		}
		localizations := map[string]any{}
		sources := []string{}
		for locale, v := range group {
			fields := map[string]any{}
			for _, k := range []string{"title", "description", "locations"} {
				if detailTruthy(v.Fields[k]) {
					fields[k] = v.Fields[k]
				}
			}
			localizations[locale] = fields
			sources = append(sources, v.Source)
		}
		sort.Strings(sources)
		selected.Fields["localizations"] = localizations
		md := selected.Fields["metadata"].(map[string]any)
		md["application_identity"] = key
		md["prospective_source_urls"] = sources
		selected.Fields["metadata"] = md
		jobs = append(jobs, selected.Fields)
	}
	return jobs, nil
}
