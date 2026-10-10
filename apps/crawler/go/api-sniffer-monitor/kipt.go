package apisniffer

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
	xhtml "golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type KIPTOptions struct {
	BoardURL, Location string
	MaxAgeDays         int
}
type KIPTBulletin struct{ URL, Posted string }
type KIPTPDFText func(context.Context, string) (string, error)

var kiptPDFPath = regexp.MustCompile(`(?i)/news/[0-9]{4}/vacancy_([0-9]{1,2})_([0-9]{1,2})_([0-9]{4})\.pdf$`)

func KIPTOptionsFromMetadata(board, metadata string) (KIPTOptions, error) {
	o := KIPTOptions{BoardURL: board, Location: "Kharkiv, Ukraine", MaxAgeDays: 30}
	u, e := url.Parse(board)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.RawPath != "" || u.Port() != "" && u.Port() != "443" || strings.TrimPrefix(strings.ToLower(u.Hostname()), "www.") != "kipt.kharkov.ua" {
		return o, ErrOptions
	}
	p := strings.ToLower(strings.TrimRight(u.Path, "/"))
	if p != "/ua/vacancy.html" && p != "/vacancy.html" {
		return o, ErrOptions
	}
	m, e := DecodeInlineMetadata(metadata)
	if e != nil {
		return o, e
	}
	localizedServiceAnnotations(m)
	for k, v := range m {
		switch k {
		case "max_age_days":
			n, e := talemetryOptionInteger(v)
			if e != nil || n < 0 {
				return o, ErrOptions
			}
			o.MaxAgeDays = n
		case "default_location":
			if detailTruthy(v) {
				d := &Document{}
				s, e := d.String(v)
				if e != nil {
					return o, ErrOptions
				}
				o.Location = s
			}
		case "rescrape_policy", "delist_threshold":
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}
func (o KIPTOptions) AlternateURL() string {
	u, _ := url.Parse(o.BoardURL)
	if strings.ToLower(strings.TrimRight(u.Path, "/")) == "/ua/vacancy.html" {
		u.Path = "/vacancy.html"
	} else {
		u.Path = "/ua/vacancy.html"
	}
	u.RawQuery = ""
	u.ForceQuery = false
	u.Fragment = ""
	u.RawFragment = ""
	return u.String()
}
func (o KIPTOptions) ResourceMatches(raw string) bool {
	if raw == o.BoardURL || raw == o.AlternateURL() {
		return true
	}
	b, _ := url.Parse(o.BoardURL)
	u, e := url.Parse(raw)
	return e == nil && u.Scheme == "https" && u.User == nil && u.RawPath == "" && u.Fragment == "" && strings.EqualFold(u.Host, b.Host) && kiptPDFPath.MatchString(u.Path)
}

func ParseKIPTBulletins(source, base, today string, maxAge int) ([]KIPTBulletin, error) {
	now, e := time.Parse("2006-01-02", today)
	if e != nil || maxAge < 0 {
		return nil, ErrOptions
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, e
	}
	b, e := url.Parse(base)
	if e != nil {
		return nil, ErrOptions
	}
	seen := map[string]string{}
	for _, node := range cascadia.QueryAll(doc, cascadia.MustCompile("a[href]")) {
		href, _ := lastNodeAttribute(node, "href")
		r, e := url.Parse(href)
		if e != nil {
			continue
		}
		u := b.ResolveReference(r)
		m := kiptPDFPath.FindStringSubmatch(u.Path)
		if len(m) != 4 {
			continue
		}
		parts := []int{}
		for _, s := range m[1:] {
			n, _ := strconv.Atoi(s)
			parts = append(parts, n)
		}
		d := time.Date(parts[2], time.Month(parts[1]), parts[0], 0, 0, 0, 0, time.UTC)
		if parts[2] < 1 || d.Year() != parts[2] || int(d.Month()) != parts[1] || d.Day() != parts[0] {
			continue
		}
		age := int((now.Unix() - d.Unix()) / 86400)
		if age >= 0 && age <= maxAge {
			seen[u.String()] = d.Format("2006-01-02")
		}
	}
	keys := []string{}
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := []KIPTBulletin{}
	for _, k := range keys {
		out = append(out, KIPTBulletin{k, seen[k]})
	}
	return out, nil
}

func kiptHTML(text string) string {
	escape := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\"", "&quot;", "'", "&#x27;")
	paragraphs := regexp.MustCompile(`\n\s*\n`).Split(text, -1)
	out := []string{}
	for _, p := range paragraphs {
		p = strings.Join(strings.FieldsFunc(p, enterpriseSpace), " ")
		if p != "" {
			out = append(out, "<p>"+escape.Replace(p)+"</p>")
		}
	}
	return strings.Join(out, "\n")
}

func kiptSyntheticURL(raw, line string) (string, error) {
	u, e := url.Parse(raw)
	if e != nil {
		return "", e
	}
	id := sha256.Sum256([]byte(cases.Fold().String(strings.Join(strings.FieldsFunc(line, enterpriseSpace), " "))))
	values, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return "", e
	}
	// Preserve the original parse_qs first-seen key order, including repeated
	// query values. The synthetic key replaces an existing _jid in place.
	keys := []string{}
	seen := map[string]bool{}
	for _, part := range strings.Split(u.RawQuery, "&") {
		if part == "" {
			continue
		}
		k, _, _ := strings.Cut(part, "=")
		key, e := url.QueryUnescape(k)
		if e != nil {
			return "", e
		}
		if !seen[key] {
			keys = append(keys, key)
			seen[key] = true
		}
	}
	if !seen["_jid"] {
		keys = append(keys, "_jid")
	}
	values["_jid"] = []string{hex.EncodeToString(id[:])[:12]}
	pairs := [][2]string{}
	for _, k := range keys {
		for _, v := range values[k] {
			pairs = append(pairs, [2]string{k, v})
		}
	}
	u.RawQuery = lastQuery(pairs)
	return u.String(), nil
}

func ParseKIPTBulletin(raw, text, posted, location string) ([]Job, error) {
	if len(text) > 16<<20 || !utf8.ValidString(text) {
		return nil, ErrInventory
	}
	if _, e := time.Parse("2006-01-02", posted); e != nil {
		return nil, ErrOptions
	}
	compile := func(pattern string) (*regexp2.Regexp, error) { return dom.CompileURLPattern(pattern) }
	block, e := compile(`(?ims)на\s+заміщення\s+вакантн(?:ої|их)\s+посад[и]?:\s*(?P<body>.+?)(?=^\s*Вимоги\s+до\s+кандидатів\s*:)`)
	if e != nil {
		return nil, e
	}
	match, e := block.FindStringMatch(text)
	if e != nil {
		return nil, e
	}
	if match == nil {
		return []Job{}, nil
	}
	commonPattern, e := compile(`(?im)^\s*Вимоги\s+до\s+кандидатів\s*:`)
	if e != nil {
		return nil, e
	}
	runes := []rune(text)
	remaining := string(runes[match.RuneIndex+match.RuneLength:])
	common, e := commonPattern.FindStringMatch(remaining)
	if e != nil {
		return nil, e
	}
	details := ""
	if common != nil {
		details = string([]rune(remaining)[common.RuneIndex:])
	}
	vacancy, e := compile(`(?ims)^\s*[-–]\s*(?P<title>.+?)\s+[-–]\s+(?P<count>\d+)\s+ваканс(?:ія|ії|ій)\b(?P<details>.*?)[;.]\s*$`)
	if e != nil {
		return nil, e
	}
	m, e := vacancy.FindStringMatch(match.GroupByName("body").String())
	jobs := []Job{}
	for m != nil && e == nil {
		title := strings.Join(strings.FieldsFunc(m.GroupByName("title").String(), enterpriseSpace), " ")
		line := strings.Join(strings.FieldsFunc(m.String(), enterpriseSpace), " ")
		count, e := strconv.Atoi(m.GroupByName("count").String())
		if e != nil {
			return nil, ErrField
		}
		if title != "" {
			u, e := kiptSyntheticURL(raw, line)
			if e != nil {
				return nil, e
			}
			description := line
			if details != "" {
				description += "\n\n" + details
			}
			jobs = append(jobs, Job{URL: u, Title: title, Description: kiptHTML(description), Locations: []string{location}, DatePosted: posted, Extras: map[string]any{"language": "uk"}, Metadata: map[string]any{"source_pdf": raw, "vacancy_count": count}})
		}
		m, e = vacancy.FindNextMatch(m)
	}
	return jobs, e
}

// Listing fallback is supplied by the worker's typed gone-status transport;
// no fallback is authorized for a transient or reserved response.
func DiscoverKIPT(ctx context.Context, o KIPTOptions, today, listingURL string, source []byte, pdf KIPTPDFText) ([]Job, error) {
	if pdf == nil || listingURL != o.BoardURL && listingURL != o.AlternateURL() {
		return nil, ErrOptions
	}
	bulletins, e := ParseKIPTBulletins(string(source), listingURL, today, o.MaxAgeDays)
	if e != nil {
		return nil, e
	}
	jobs := []Job{}
	for _, b := range bulletins {
		if !o.ResourceMatches(b.URL) {
			return nil, ErrInventory
		}
		text, e := pdf(ctx, b.URL)
		if e != nil {
			return nil, e
		}
		parsed, e := ParseKIPTBulletin(b.URL, text, b.Posted, o.Location)
		if e != nil {
			return nil, e
		}
		if len(parsed) == 0 {
			return nil, ErrInventory
		}
		jobs = append(jobs, parsed...)
	}
	return jobs, nil
}
