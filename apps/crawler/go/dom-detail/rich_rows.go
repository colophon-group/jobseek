package dom

import (
	"context"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	"github.com/dlclark/regexp2/v2"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type RichRowsPolicy struct {
	Include      *regexp2.Regexp
	AllowEmpty   bool
	JoinURL      func(string, string) (string, error)
	Canonicalize func(string) (string, error)
}

type RichRowJob struct {
	URL         string            `json:"url"`
	Title       string            `json:"title"`
	Description *string           `json:"description"`
	Locations   []string          `json:"locations"`
	Metadata    map[string]string `json:"metadata"`
}

func richRowsText(n *html.Node, separator string) string {
	if n == nil {
		return ""
	}
	parts := []string{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			if text := trim(n.Data); text != "" {
				parts = append(parts, text)
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(n)
	return strings.Join(parts, separator)
}
func richRowSelect(n *html.Node, selector string) *html.Node {
	if n == nil || selector == "" {
		return nil
	}
	return cascadia.Query(n, cascadia.MustCompile(selector))
}
func richRowSameOrigin(a, b string) bool {
	x, e := url.Parse(a)
	if e != nil {
		return false
	}
	y, e := url.Parse(b)
	if e != nil {
		return false
	}
	port := func(u *url.URL) string {
		if u.Port() != "" {
			return u.Port()
		}
		if u.Scheme == "https" {
			return "443"
		}
		if u.Scheme == "http" {
			return "80"
		}
		return ""
	}
	return strings.EqualFold(x.Scheme, y.Scheme) && strings.EqualFold(x.Hostname(), y.Hostname()) && port(x) == port(y)
}
func richRowsSection(doc *html.Node, rows []*html.Node, c *RichRowsConfig) ([]*html.Node, error) {
	if c.SectionStart == nil {
		return rows, nil
	}
	positions := map[*html.Node]int{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		positions[n] = len(positions)
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(doc)
	boundary := func(b *RichRowBoundary, after int) (int, error) {
		found := -1
		for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile(b.Selector)) {
			pos := positions[n]
			if pos <= after {
				continue
			}
			text := strings.Join(strings.Fields(richRowsText(n, " ")), " ")
			if b.Text != "" && !strings.Contains(cases.Fold().String(text), cases.Fold().String(b.Text)) {
				continue
			}
			if found >= 0 {
				return 0, ErrRichRows
			}
			found = pos
		}
		if found < 0 {
			return 0, ErrRichRows
		}
		return found, nil
	}
	start, e := boundary(c.SectionStart, -1)
	if e != nil {
		return nil, e
	}
	end, e := boundary(c.SectionEnd, start)
	if e != nil {
		return nil, e
	}
	out := []*html.Node{}
	for _, row := range rows {
		if pos := positions[row]; start < pos && pos < end {
			out = append(out, row)
		}
	}
	return out, nil
}

// ParseRichRows returns no partial inventory when a row, boundary, lifecycle
// assignment or advertised total is inconsistent. URL joining and canonical
// rewriting are supplied by the process's existing inventory implementation.
func ParseRichRows(ctx context.Context, source, base string, c *RichRowsConfig, p RichRowsPolicy) ([]RichRowJob, error) {
	if c == nil || p.JoinURL == nil {
		return nil, ErrRichRows
	}
	doc, e := html.Parse(strings.NewReader(source))
	if e != nil {
		return nil, e
	}
	total := -1
	if c.TotalSelector != "" {
		text := strings.Join(strings.Fields(richRowsText(richRowSelect(doc, c.TotalSelector), " ")), " ")
		if !regexp.MustCompile(`^(0|[1-9][\p{Nd}]{0,5})$`).MatchString(text) {
			return nil, ErrRichRows
		}
		text = strings.Map(func(r rune) rune {
			for _, span := range unicode.Digit.R16 {
				if uint32(r) >= uint32(span.Lo) && uint32(r) <= uint32(span.Hi) && (uint32(r)-uint32(span.Lo))%uint32(span.Stride) == 0 {
					return '0' + rune((uint32(r)-uint32(span.Lo))/uint32(span.Stride)%10)
				}
			}
			for _, span := range unicode.Digit.R32 {
				if uint32(r) >= span.Lo && uint32(r) <= span.Hi && (uint32(r)-span.Lo)%span.Stride == 0 {
					return '0' + rune((uint32(r)-span.Lo)/span.Stride%10)
				}
			}
			return r
		}, text)
		total, e = strconv.Atoi(text)
		if e != nil || total > 50000 {
			return nil, ErrRichRows
		}
	}
	rows, e := richRowsSection(doc, cascadia.QueryAll(doc, cascadia.MustCompile(c.RowSelector)), c)
	if e != nil {
		return nil, e
	}
	if len(rows) == 0 {
		if p.AllowEmpty && total <= 0 {
			return []RichRowJob{}, nil
		}
		return nil, ErrRichRows
	}
	jobs := []RichRowJob{}
	indices := map[string]int{}
	for _, row := range rows {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		if c.RequiredSelector != "" && richRowSelect(row, c.RequiredSelector) == nil {
			continue
		}
		if c.RowPattern != nil {
			ok, e := c.RowPattern.MatchString(strings.Join(strings.Fields(richRowsText(row, " ")), " "))
			if e != nil {
				return nil, e
			}
			if !ok {
				continue
			}
		}
		link := row
		if c.LinkSelector != "" {
			link = richRowSelect(row, c.LinkSelector)
		}
		href := icimsAttr(link, c.LinkAttr)
		titleNode := link
		if c.TitleSelector != "" {
			titleNode = richRowSelect(row, c.TitleSelector)
		}
		title := richRowsText(titleNode, " ")
		for _, replace := range c.Replacements {
			title = strings.ReplaceAll(title, replace.Source, replace.Replacement)
		}
		if title != "" && c.TitlePattern != nil {
			match, e := c.TitlePattern.FindStringMatch(title)
			if e != nil {
				return nil, e
			}
			title = ""
			if match != nil {
				group := match.GroupByNumber(1)
				if group == nil {
					return nil, ErrRichRows
				}
				title = trim(group.String())
			}
		}
		if href == "" || title == "" {
			return nil, ErrRichRows
		}
		raw := ""
		if c.URLTemplate != "" {
			raw = strings.ReplaceAll(c.URLTemplate, "{value}", href)
			if !richRowSameOrigin(raw, base) {
				return nil, ErrRichRows
			}
		} else {
			raw, e = p.JoinURL(base, href)
			if e != nil {
				return nil, e
			}
		}
		if !strings.HasPrefix(raw, "http") {
			return nil, ErrRichRows
		}
		if p.Include != nil {
			ok, e := p.Include.MatchString(raw)
			if e != nil {
				return nil, e
			}
			if !ok {
				continue
			}
		}
		if p.Canonicalize != nil {
			raw, e = p.Canonicalize(raw)
			if e != nil {
				return nil, e
			}
		}
		if len(c.ActiveURLs) > 0 {
			_, e := url.Parse(raw)
			if e != nil {
				return nil, e
			}
			// urllib preserves raw Unicode and escapes when removing query and
			// fragment decoration; net/url serialization would escape them.
			if end := strings.IndexAny(raw, "?#"); end >= 0 {
				raw = raw[:end]
			}
			if c.InactiveURLs[raw] {
				continue
			}
			if !c.ActiveURLs[raw] {
				return nil, ErrRichRows
			}
		}
		parts := []string{}
		seen := map[string]bool{}
		for i, selector := range c.LocationSelectors {
			value := richRowsText(richRowSelect(row, selector), c.LocationSeparator)
			values := []string{}
			if c.LocationSeparator == " " {
				if value != "" {
					values = append(values, value)
				}
			} else {
				for _, part := range strings.Split(value, c.LocationSeparator) {
					if part = trim(part); part != "" {
						values = append(values, part)
					}
				}
			}
			if pattern := c.LocationPatterns[i]; pattern != nil {
				filtered := []string{}
				for _, part := range values {
					ok, e := pattern.MatchString(part)
					if e != nil {
						return nil, e
					}
					if ok {
						filtered = append(filtered, part)
					}
				}
				values = filtered
			}
			if len(values) == 0 && c.LocationMode == "all" && !c.AllowMissingLocations {
				return nil, ErrRichRows
			}
			for _, part := range values {
				if !seen[part] {
					seen[part] = true
					parts = append(parts, part)
				}
			}
			if len(values) > 0 && c.LocationMode == "first" {
				break
			}
		}
		if len(c.LocationSelectors) > 0 && len(parts) == 0 && !c.AllowMissingLocations {
			return nil, ErrRichRows
		}
		var locations []string
		if len(parts) > 0 {
			if c.LocationSeparator == " " {
				locations = []string{strings.Join(parts, ", ")}
			} else {
				locations = parts
			}
		} else if len(c.DefaultLocations) > 0 {
			locations = append([]string{}, c.DefaultLocations...)
		}
		var metadata map[string]string
		for field, selector := range c.MetadataSelectors {
			value := richRowsText(richRowSelect(row, selector), " ")
			if value == "" {
				return nil, ErrRichRows
			}
			if metadata == nil {
				metadata = map[string]string{}
			}
			metadata[field] = value
		}
		var description *string
		node := (*html.Node)(nil)
		if c.DescriptionSelector != "" {
			node = richRowSelect(row, c.DescriptionSelector)
		} else if c.DescriptionNextSelector != "" {
			node = row.NextSibling
			for node != nil && node.Type == html.TextNode {
				node = node.NextSibling
			}
			if node == nil || node.Type != html.ElementNode || !cascadia.MustCompile(c.DescriptionNextSelector).Match(node) {
				return nil, ErrRichRows
			}
		}
		if c.DescriptionSelector != "" || c.DescriptionNextSelector != "" {
			if node == nil || richRowsText(node, " ") == "" {
				return nil, ErrRichRows
			}
			var out strings.Builder
			if e := renderHTML(&out, node); e != nil {
				return nil, e
			}
			text := trim(out.String())
			description = &text
		}
		job := RichRowJob{raw, title, description, locations, metadata}
		if index, ok := indices[raw]; ok {
			if !reflect.DeepEqual(jobs[index], job) && p.Canonicalize == nil {
				if c.DuplicatePolicy != "prefer_longer_title" {
					return nil, ErrRichRows
				}
				if utf8.RuneCountInString(title) <= utf8.RuneCountInString(jobs[index].Title) {
					continue
				}
			}
			jobs[index] = job
		} else {
			indices[raw] = len(jobs)
			jobs = append(jobs, job)
		}
	}
	if total >= 0 && len(jobs) != total || len(jobs) == 0 {
		return nil, ErrRichRows
	}
	if p.Canonicalize != nil {
		sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
	}
	return jobs, nil
}
