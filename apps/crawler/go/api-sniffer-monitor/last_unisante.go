package apisniffer

import (
	"context"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	xhtml "golang.org/x/net/html"
	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

const unisanteOrigin = "https://emploi.unisante.ch"

var unisanteAliases = []string{unisanteOrigin + "/index.php/offres", unisanteOrigin + "/offres"}
var unisanteDetailPath = regexp.MustCompile(`^/(?:index\.php/)?offre/([^/]+)/?$`)
var unisanteReference = regexp.MustCompile(`(?i)\bRéférence\s*:\s*([1-9][0-9]{0,8})\b`)
var unisanteNumericSlug = regexp.MustCompile(`^([1-9][0-9]*)-`)
var unisanteDeadlineLabel = regexp.MustCompile(`(?i)\bDélai\s+de\s+postulation(?:\s*/\s*Application\s+deadline)?\s*:\s*`)
var unisanteDeadlineDate = regexp.MustCompile(`^([0-9]{1,2}\s*[-./]\s*[0-9]{1,2}\s*[-./]\s*[0-9]{4}|[0-9]{1,2}\s+[A-Za-zÀ-ÿ]+\s+[0-9]{4})\b`)
var unisanteNumericDate = regexp.MustCompile(`^([0-9]{1,2})\s*[-./]\s*([0-9]{1,2})\s*[-./]\s*([0-9]{4})$`)
var unisanteHiddenStyle = regexp.MustCompile(`(?:^|;)display:none(?:!important)?(?:;|$)`)

type UnisanteListingJob struct{ Slug, Title string }

func (j UnisanteListingJob) URL() string    { return unisanteOrigin + "/index.php/offre/" + j.Slug }
func unisanteText(s string) string          { return strings.Join(strings.FieldsFunc(s, enterpriseSpace), " ") }
func unisanteNodeText(n *xhtml.Node) string { return unisanteText(paylocityText(n, " ")) }
func unisanteClean(v any) string            { s, _ := v.(string); return unisanteText(s) }
func unisanteFold(s string) string {
	var b strings.Builder
	for _, r := range norm.NFKD.String(cases.Fold().String(s)) {
		if !unicode.Is(unicode.Mn, r) && !unicode.Is(unicode.Me, r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func unisanteSlugFromHref(raw, base string) string {
	b, e := url.Parse(base)
	if e != nil {
		return ""
	}
	r, e := url.Parse(raw)
	if e != nil {
		return ""
	}
	u := b.ResolveReference(r)
	if u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "emploi.unisante.ch") || u.User != nil || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" {
		return ""
	}
	m := unisanteDetailPath.FindStringSubmatch(u.Path)
	if len(m) != 2 || !unisanteSlug.MatchString(m[1]) {
		return ""
	}
	return m[1]
}
func unisanteDocument(source, raw string, max int) (*xhtml.Node, error) {
	if len(source) == 0 || len(source) > max {
		return nil, ErrInventory
	}
	p, e := dom.ClassifyDocument(source, dom.Object{}, raw)
	if e != nil || p["classification"] != "okay" {
		return nil, ErrInventory
	}
	return xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
}
func ParseUnisanteListing(source, raw string) (map[string]UnisanteListingJob, error) {
	doc, e := unisanteDocument(source, raw, 512*1024)
	if e != nil {
		return nil, e
	}
	main := cascadia.Query(doc, cascadia.MustCompile("main#main"))
	if main == nil {
		return nil, ErrInventory
	}
	heading := cascadia.Query(main, cascadia.MustCompile("h1"))
	if heading == nil || unisanteNodeText(heading) != "Nos offres d'emploi" || cascadia.Query(main, cascadia.MustCompile(".row.offres-items")) == nil || cascadia.Query(main, cascadia.MustCompile(`select#offres-filter option[value='0']`)) == nil {
		return nil, ErrInventory
	}
	empty := cascadia.Query(main, cascadia.MustCompile("#no-ads"))
	if empty == nil || unisanteNodeText(empty) != "Aucune offre n'est disponible pour le moment." || cascadia.Query(main, cascadia.MustCompile(`.pagination, a[rel='next'], link[rel='next']`)) != nil {
		return nil, ErrInventory
	}
	cards := cascadia.QueryAll(main, cascadia.MustCompile(".row.offres-items .offres-item"))
	links := cascadia.QueryAll(main, cascadia.MustCompile(".row.offres-items .offres-item a.box-job__header_link[href]"))
	if len(cards) != len(links) || len(links) > 50 {
		return nil, ErrInventory
	}
	jobs := map[string]UnisanteListingJob{}
	for _, link := range links {
		href, _ := lastNodeAttribute(link, "href")
		slug := unisanteSlugFromHref(href, raw)
		title, _ := lastNodeAttribute(link, "title")
		title = unisanteText(title)
		if title == "" {
			title = unisanteNodeText(link)
		}
		if slug == "" || title == "" {
			return nil, ErrInventory
		}
		if _, ok := jobs[slug]; ok {
			return nil, ErrInventory
		}
		jobs[slug] = UnisanteListingJob{slug, title}
	}
	recognizable := map[string]bool{}
	for _, a := range cascadia.QueryAll(main, cascadia.MustCompile("a[href]")) {
		href, _ := lastNodeAttribute(a, "href")
		if slug := unisanteSlugFromHref(href, raw); slug != "" {
			recognizable[slug] = true
		}
	}
	if len(recognizable) != len(jobs) {
		return nil, ErrInventory
	}
	for slug := range recognizable {
		if _, ok := jobs[slug]; !ok {
			return nil, ErrInventory
		}
	}
	_, hidden := lastNodeAttribute(empty, "hidden")
	class, _ := lastNodeAttribute(empty, "class")
	aria, _ := lastNodeAttribute(empty, "aria-hidden")
	style, _ := lastNodeAttribute(empty, "style")
	for _, c := range strings.Fields(class) {
		hidden = hidden || c == "d-none"
	}
	compact := strings.Join(strings.FieldsFunc(strings.ToLower(style), enterpriseSpace), "")
	hidden = hidden || strings.EqualFold(strings.TrimFunc(aria, enterpriseSpace), "true") || unisanteHiddenStyle.MatchString(compact)
	if len(jobs) > 0 && !hidden || len(jobs) == 0 && (hidden || len(cards) > 0) {
		return nil, ErrInventory
	}
	return jobs, nil
}
func unisanteValidDate(year, month, day int) (string, error) {
	d := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if year < 1 || year > 9999 || d.Year() != year || int(d.Month()) != month || d.Day() != day {
		return "", ErrField
	}
	return d.Format("2006-01-02"), nil
}
func UnisanteDeadline(text string) (string, error) {
	loc := unisanteDeadlineLabel.FindStringIndex(text)
	if loc == nil {
		return "", nil
	}
	value := unisanteDeadlineDate.FindStringSubmatch(strings.TrimLeftFunc(text[loc[1]:], enterpriseSpace))
	if len(value) != 2 {
		return "", ErrField
	}
	numeric := unisanteNumericDate.FindStringSubmatch(value[1])
	var day, month, year int
	if len(numeric) == 4 {
		day, _ = strconv.Atoi(numeric[1])
		month, _ = strconv.Atoi(numeric[2])
		year, _ = strconv.Atoi(numeric[3])
	} else {
		words := strings.FieldsFunc(value[1], enterpriseSpace)
		months := map[string]int{"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
		if len(words) != 3 {
			return "", ErrField
		}
		day, _ = strconv.Atoi(words[0])
		month = months[unisanteFold(words[1])]
		year, _ = strconv.Atoi(words[2])
	}
	return unisanteValidDate(year, month, day)
}
func unisantePostingFields(doc *xhtml.Node) (any, any, []string, error) {
	postings := []map[string]any{}
	var find func(any)
	find = func(v any) {
		switch x := v.(type) {
		case []any:
			for _, item := range x {
				find(item)
			}
		case map[string]any:
			found := x["@type"] == "JobPosting"
			if types, ok := x["@type"].([]any); ok {
				for _, kind := range types {
					if kind == "JobPosting" {
						found = true
					}
				}
			}
			if found {
				postings = append(postings, x)
			}
			switch graph := x["@graph"].(type) {
			case []any:
				find(graph)
			case map[string]any:
				find(graph)
			}
		}
	}
	for _, s := range cascadia.QueryAll(doc, cascadia.MustCompile("script[type]")) {
		kind, _ := lastNodeAttribute(s, "type")
		if !strings.EqualFold(kind, "application/ld+json") {
			continue
		}
		raw := strings.TrimFunc(remainingNodeText(s), enterpriseSpace)
		if raw == "" {
			continue
		}
		d, e := Decode([]byte(raw))
		if e != nil {
			return nil, nil, nil, e
		}
		find(d.Value)
	}
	if len(postings) != 1 {
		return nil, nil, nil, ErrInventory
	}
	p := postings[0]
	org, ok := p["hiringOrganization"].(map[string]any)
	if !ok || !strings.Contains(unisanteFold(unisanteClean(org["name"])), "unisante") {
		return nil, nil, nil, ErrInventory
	}
	same, e := url.Parse(unisanteClean(org["sameAs"]))
	if e != nil || same.Hostname() != "unisante.ch" && same.Hostname() != "www.unisante.ch" {
		return nil, nil, nil, ErrInventory
	}
	var posted, employment any
	if date := unisanteClean(p["datePosted"]); date != "" {
		if len(date) > 10 {
			date = date[:10]
		}
		d, e := time.Parse("2006-01-02", date)
		if e != nil {
			return nil, nil, nil, ErrField
		}
		posted = d.Format("2006-01-02")
	}
	if values, ok := p["employmentType"].([]any); ok {
		parts := []string{}
		for _, v := range values {
			if text := unisanteClean(v); text != "" {
				parts = append(parts, text)
			}
		}
		if len(parts) > 0 {
			employment = strings.Join(parts, ", ")
		}
	} else if s := unisanteClean(p["employmentType"]); s != "" {
		employment = s
	}
	locations := []string{}
	items, ok := p["jobLocation"].([]any)
	if !ok {
		items = []any{p["jobLocation"]}
	}
	for _, item := range items {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		a, ok := m["address"].(map[string]any)
		if !ok {
			continue
		}
		country := a["addressCountry"]
		if c, ok := country.(map[string]any); ok {
			country = c["name"]
			if !detailTruthy(country) {
				country = c["addressCountry"]
			}
		}
		parts := []string{}
		for _, s := range []string{unisanteClean(a["addressLocality"]), unisanteClean(country)} {
			if s != "" {
				parts = append(parts, s)
			}
		}
		location := strings.Join(parts, ", ")
		if location != "" {
			duplicate := false
			for _, old := range locations {
				duplicate = duplicate || old == location
			}
			if !duplicate {
				locations = append(locations, location)
			}
		}
	}
	if len(locations) == 0 {
		locations = nil
	}
	return posted, employment, locations, nil
}
func ParseUnisanteDetail(source string, listing UnisanteListingJob, today string, normalize SmallDescriptionNormalizer) (*Job, error) {
	if normalize == nil {
		return nil, ErrInventory
	}
	doc, e := unisanteDocument(source, listing.URL(), 1024*1024)
	if e != nil {
		return nil, e
	}
	main := cascadia.Query(doc, cascadia.MustCompile("main#main"))
	if main == nil {
		return nil, ErrInventory
	}
	scopes := []*xhtml.Node{}
	for _, n := range cascadia.QueryAll(main, cascadia.MustCompile(".col-md-10")) {
		if unisanteReference.MatchString(unisanteNodeText(n)) {
			scopes = append(scopes, n)
		}
	}
	if len(scopes) != 1 {
		return nil, ErrInventory
	}
	scope := scopes[0]
	visible := unisanteNodeText(scope)
	references := map[string]bool{}
	for _, m := range unisanteReference.FindAllStringSubmatch(visible, -1) {
		references[m[1]] = true
	}
	if len(references) != 1 {
		return nil, ErrInventory
	}
	reference := ""
	for r := range references {
		reference = r
	}
	if m := unisanteNumericSlug.FindStringSubmatch(listing.Slug); len(m) == 2 && m[1] != reference {
		return nil, ErrInventory
	}
	deadline, e := UnisanteDeadline(visible)
	if e != nil {
		return nil, e
	}
	for _, n := range cascadia.QueryAll(scope, cascadia.MustCompile("form,script,style,noscript,input,button")) {
		if n.Parent != nil {
			n.Parent.RemoveChild(n)
		}
	}
	body, e := dom.InnerHTML(scope)
	if e != nil {
		return nil, e
	}
	description, e := normalize(body)
	if e != nil || description == nil {
		return nil, ErrField
	}
	normalized, e := xhtml.ParseWithOptions(strings.NewReader(*description), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, e
	}
	text := unisanteNodeText(normalized)
	if utf8.RuneCountInString(text) < 300 {
		return nil, ErrField
	}
	for _, marker := range []string{"Ã", "â€™", "â€“", "â€”", "\ufffd"} {
		if strings.Contains(text, marker) {
			return nil, ErrField
		}
	}
	// Read the original document: pruning may remove nested JSON-LD scripts.
	original, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, e
	}
	posted, employment, locations, e := unisantePostingFields(original)
	if e != nil {
		return nil, e
	}
	if _, e := time.Parse("2006-01-02", today); e != nil {
		return nil, ErrField
	}
	if deadline != "" && deadline < today {
		return nil, nil
	}
	metadata := map[string]any{"provider_reference": reference, "detail_slug": listing.Slug, "detail_url": listing.URL()}
	if deadline != "" {
		metadata["application_deadline"] = deadline
	}
	return &Job{URL: listing.URL(), Title: listing.Title, Description: *description, Locations: locations, EmploymentType: employment, DatePosted: posted, Metadata: metadata, SourceIdentity: "unisante:emploi:" + reference, Extras: map[string]any{"language": "fr"}}, nil
}
func DiscoverUnisante(ctx context.Context, o LastHTTPOptions, today string, fetch SmallProviderFetch, normalize SmallDescriptionNormalizer) ([]Job, error) {
	if o.Provider != "unisante" || fetch == nil || normalize == nil {
		return nil, ErrInventory
	}
	inventories := make([]map[string]UnisanteListingJob, 2)
	for i, raw := range unisanteAliases {
		body, e := fetch(ctx, Request{Method: "GET", URL: raw})
		if e != nil {
			return nil, e
		}
		rows, e := ParseUnisanteListing(string(body), raw)
		if e != nil {
			return nil, e
		}
		inventories[i] = rows
	}
	if !reflect.DeepEqual(inventories[0], inventories[1]) {
		return nil, ErrInventory
	}
	slugs := []string{}
	for slug := range inventories[0] {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	byReference := map[string]Job{}
	for start := 0; start < len(slugs); start += 3 {
		type result struct {
			job *Job
			e   error
		}
		count := min(3, len(slugs)-start)
		channels := make([]chan result, count)
		for i := 0; i < count; i++ {
			channels[i] = make(chan result, 1)
			go func(index, slot int) {
				listing := inventories[0][slugs[index]]
				body, e := fetch(ctx, Request{Method: "GET", URL: listing.URL()})
				if e != nil {
					channels[slot] <- result{nil, e}
					return
				}
				j, e := ParseUnisanteDetail(string(body), listing, today, normalize)
				channels[slot] <- result{j, e}
			}(start+i, i)
		}
		results := make([]result, count)
		for i, c := range channels {
			select {
			case results[i] = <-c:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		for _, r := range results {
			if r.e != nil {
				return nil, r.e
			}
			if r.job == nil {
				continue
			}
			j := *r.job
			ref := j.Metadata["provider_reference"].(string)
			old, ok := byReference[ref]
			if !ok {
				byReference[ref] = j
				continue
			}
			if old.Title != j.Title {
				return nil, ErrInventory
			}
			numeric := strings.HasPrefix(j.Metadata["detail_slug"].(string), ref+"-")
			oldNumeric := strings.HasPrefix(old.Metadata["detail_slug"].(string), ref+"-")
			if numeric && !oldNumeric || numeric == oldNumeric && j.URL < old.URL {
				byReference[ref] = j
			}
		}
	}
	refs := []string{}
	for ref := range byReference {
		refs = append(refs, ref)
	}
	sort.Slice(refs, func(i, j int) bool { a, _ := strconv.Atoi(refs[i]); b, _ := strconv.Atoi(refs[j]); return a < b })
	jobs := make([]Job, 0, len(refs))
	for _, ref := range refs {
		jobs = append(jobs, byReference[ref])
	}
	return jobs, nil
}
