package apisniffer

import (
	"context"
	"fmt"
	stdhtml "html"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type InlineInventory struct {
	Jobs                []Job
	Truncated           bool
	VerifiedEmptyReason string
}

func inlineNormalized(s string) string {
	return strings.Join(strings.FieldsFunc(s, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }), " ")
}
func inlineNodeText(n *html.Node) string {
	parts := []string{}
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			if s := inlineTrim(n.Data); s != "" {
				parts = append(parts, s)
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(n)
	return strings.Join(parts, " ")
}
func inlinePlainDescription(s string) (string, error) {
	tree, err := html.ParseWithOptions(strings.NewReader(s), html.ParseOptionEnableScripting(false))
	if err != nil {
		return "", ErrInventory
	}
	body := cascadia.Query(tree, cascadia.MustCompile("body"))
	if body != nil {
		s = inlineNodeText(body)
	}
	return inlineNormalized(stdhtml.UnescapeString(s)), nil
}
func inlineSelectedNodes(tree *html.Node, selector string) []*html.Node {
	if selector == "" {
		return nil
	}
	return cascadia.QueryAll(tree, cascadia.MustCompile(selector))
}
func inlineAttribute(n *html.Node, key string) (string, bool) {
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val, true
		}
	}
	return "", false
}
func inlineSourceValues(tree *html.Node, o InlineOptions, boardURL string) ([]string, []string, error) {
	ids, urls := []string{}, []string{}
	if o.SourceSelector != "" {
		nodes := inlineSelectedNodes(tree, o.SourceSelector)
		if len(nodes) == 0 || len(nodes) > 500 {
			return nil, nil, ErrInventory
		}
		seen := map[string]bool{}
		for _, n := range nodes {
			raw, ok := inlineAttribute(n, o.SourceAttribute)
			if !ok {
				return nil, nil, ErrInventory
			}
			match, err := o.SourcePattern.FindStringMatch(raw)
			if err != nil || match == nil || match.String() != raw {
				return nil, nil, ErrInventory
			}
			group := match.GroupByNumber(1)
			if group == nil || len(group.Captures) == 0 {
				return nil, nil, ErrInventory
			}
			id := group.String()
			if id == "" || len([]rune(id)) > 512 || seen[id] {
				return nil, nil, ErrInventory
			}
			for _, r := range id {
				if r < 0x20 {
					return nil, nil, ErrInventory
				}
			}
			seen[id] = true
			ids = append(ids, id)
		}
	}
	if o.SourceURLSelector != "" {
		nodes := inlineSelectedNodes(tree, o.SourceURLSelector)
		if len(nodes) == 0 || len(nodes) > 500 {
			return nil, nil, ErrInventory
		}
		board, err := url.Parse(boardURL)
		if err != nil {
			return nil, nil, ErrInventory
		}
		seen := map[string]bool{}
		for _, n := range nodes {
			raw, ok := inlineAttribute(n, o.SourceURLAttribute)
			if !ok {
				return nil, nil, ErrInventory
			}
			raw = inlineTrim(stdhtml.UnescapeString(raw))
			if raw == "" || len([]rune(raw)) > 4096 {
				return nil, nil, ErrInventory
			}
			for _, r := range raw {
				if r < 0x20 {
					return nil, nil, ErrInventory
				}
			}
			ref, err := url.Parse(raw)
			if err != nil {
				return nil, nil, ErrInventory
			}
			absolute := board.ResolveReference(ref)
			absolute.Fragment = ""
			absolute.RawFragment = ""
			if absolute.User != nil || !strings.EqualFold(absolute.Scheme, board.Scheme) || !strings.EqualFold(absolute.Hostname(), board.Hostname()) || absolute.Port() != board.Port() || (absolute.Scheme != "http" && absolute.Scheme != "https") {
				return nil, nil, ErrInventory
			}
			s := absolute.String()
			if seen[s] {
				return nil, nil, ErrInventory
			}
			seen[s] = true
			urls = append(urls, s)
		}
	}
	return ids, urls, nil
}

var inlineOrdinal = regexp.MustCompile(`(?i)([0-9])(?:st|nd|rd|th)\b`)

func inlineDeadline(value any, format string) (time.Time, error) {
	s, ok := value.(string)
	if !ok || inlineTrim(s) == "" {
		return time.Time{}, ErrInventory
	}
	s = inlineOrdinal.ReplaceAllString(inlineTrim(s), "${1}")
	if format != "" {
		// The admitted deterministic strptime formats use English month names and
		// numeric calendar fields. Unsupported directives fail rather than guess.
		layouts := map[string]string{"%Y-%m-%d": "2006-1-2", "%d.%m.%Y": "2.1.2006", "%B %d, %Y": "January 2, 2006", "%d %B %Y": "2 January 2006", "%d %b %Y": "2 Jan 2006", "%m/%d/%Y": "1/2/2006", "%d/%m/%Y": "2/1/2006", "%Y%m%d": "20060102"}
		layout, ok := layouts[format]
		if !ok {
			return time.Time{}, ErrInventory
		}
		parsed, err := time.Parse(layout, inlineNormalized(s))
		if err != nil {
			return time.Time{}, ErrInventory
		}
		return parsed, nil
	}
	for _, layout := range []string{"2006-01-02", "20060102", time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02T15:04", "2006-01-02 15:04", "2006-01-02T15:04:05.999999999"} {
		if parsed, err := time.Parse(layout, s); err == nil {
			return time.Date(parsed.Year(), parsed.Month(), parsed.Day(), 0, 0, 0, 0, time.UTC), nil
		}
	}
	return time.Time{}, ErrInventory
}
func inlineResolveDeadline(row, defaults map[string]any, description string, o InlineOptions) (time.Time, error) {
	if row["valid_through"] != nil {
		return inlineDeadline(row["valid_through"], o.DateFormat)
	}
	for _, p := range o.Deadlines {
		match, err := p.re.FindStringMatch(description)
		if err != nil {
			return time.Time{}, ErrInventory
		}
		if match != nil {
			group := match.GroupByNumber(1)
			if group == nil || len(group.Captures) == 0 {
				return time.Time{}, ErrInventory
			}
			return inlineDeadline(inlineTrim(group.String()), p.format)
		}
	}
	if defaults["valid_through"] != nil {
		return inlineDeadline(defaults["valid_through"], o.DateFormat)
	}
	if o.ExcludeExpired {
		return time.Time{}, ErrInventory
	}
	return time.Time{}, nil
}
func inlineLocations(value any, preserve bool) ([]string, error) {
	if !detailTruthy(value) {
		return nil, nil
	}
	if s, ok := value.(string); ok {
		if preserve {
			return []string{inlineTrim(s)}, nil
		}
		out := []string{}
		for _, p := range strings.Split(s, ",") {
			if s := inlineTrim(p); s != "" {
				out = append(out, s)
			}
		}
		return out, nil
	}
	if a, ok := value.([]any); ok {
		out := []string{}
		for _, v := range a {
			s, ok := v.(string)
			if !ok {
				return nil, ErrInventory
			}
			out = append(out, s)
		}
		return out, nil
	}
	return nil, ErrInventory
}
func inlineFallback(a, b any) any {
	if detailTruthy(a) {
		return a
	}
	return b
}

// ParseInlineDocument is pure inventory assembly. Transport ownership, cookies,
// publisher policy and canonical writes stay with the sealed native worker.
func ParseInlineDocument(ctx context.Context, source, boardURL string, o InlineOptions, now time.Time) (InlineInventory, error) {
	result := InlineInventory{Jobs: []Job{}}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if !validInlineIdentityBase(boardURL) {
		return result, ErrOptions
	}
	if len(o.Steps) == 0 {
		return result, nil
	}
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return result, ErrInventory
	}
	ids, urls, err := inlineSourceValues(tree, o, boardURL)
	if err != nil {
		return result, err
	}
	elements, err := inlineScopedElements(source, o.Start, o.End, o.IncludeHidden)
	if err != nil {
		return result, err
	}
	authoritativeEmpty := false
	if o.EmptySelector != "" {
		for _, n := range inlineSelectedNodes(tree, o.EmptySelector) {
			if strings.Contains(cases.Fold().String(inlineNormalized(inlineNodeText(n))), cases.Fold().String(o.EmptyText)) {
				authoritativeEmpty = true
				break
			}
		}
		if len(inlineSelectedNodes(tree, o.NonemptySelector)) > 0 {
			authoritativeEmpty = false
		}
	}
	if authoritativeEmpty && !o.EmptyRequiresNoJobs {
		return result, nil
	}
	rows, truncated, err := inlineWalkRows(elements, o.Steps, o.Item)
	if err != nil {
		return result, err
	}
	if len(rows) == 0 {
		if o.EmptyText != "" || o.RequireZeroProof {
			return result, ErrInventory
		}
		return result, nil
	}
	seen := map[string]int{}
	expired, processed := 0, 0
	today := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	for i, row := range rows {
		if ctx.Err() != nil {
			return InlineInventory{}, ctx.Err()
		}
		processed++
		title, ok := row["title"].(string)
		if !ok {
			return InlineInventory{}, ErrInventory
		}
		var id, sourceURL string
		if o.SourceSelector != "" {
			if i >= len(ids) {
				return InlineInventory{}, ErrInventory
			}
			id = ids[i]
		}
		if o.SourceURLSelector != "" {
			if i >= len(urls) {
				return InlineInventory{}, ErrInventory
			}
			sourceURL = urls[i]
		}
		if o.ExcludeTitles[title] {
			continue
		}
		if o.ExcludeTitle != nil {
			matched, err := o.ExcludeTitle.MatchString(title)
			if err != nil {
				return InlineInventory{}, ErrInventory
			}
			if matched {
				continue
			}
		}
		description, _ := row["description"].(string)
		if description == "" && o.DescriptionFromTitle {
			description = title
		}
		if description != "" && o.ExcludeDescription != nil {
			plain, err := inlinePlainDescription(description)
			if err != nil {
				return InlineInventory{}, err
			}
			matched, err := o.ExcludeDescription.MatchString(plain)
			if err != nil {
				return InlineInventory{}, ErrInventory
			}
			if matched {
				continue
			}
		}
		var stable *string
		if o.StableField != "" {
			s, ok := row[o.StableField].(string)
			if !ok || inlineTrim(s) == "" {
				return InlineInventory{}, ErrInventory
			}
			s = inlineTrim(s)
			stable = &s
		}
		if sourceURL == "" {
			if id != "" {
				sourceURL, err = InlineIdentityURL(boardURL, id)
			} else {
				sourceURL, err = InlineSyntheticURL(boardURL, title, seen, stable)
			}
			if err != nil {
				return InlineInventory{}, err
			}
		}
		defaults := map[string]any{}
		for k, v := range o.Defaults {
			defaults[k] = v
		}
		if detailTruthy(o.DefaultsByTitle[title]) {
			byTitle, ok := o.DefaultsByTitle[title].(map[string]any)
			if !ok {
				return InlineInventory{}, ErrInventory
			}
			for k, v := range byTitle {
				defaults[k] = v
			}
		}
		deadline, err := inlineResolveDeadline(row, defaults, description, o)
		if err != nil {
			return InlineInventory{}, err
		}
		if o.ExcludeExpired && !deadline.IsZero() && deadline.Before(today) {
			expired += o.Positions
			continue
		}
		locations, err := inlineLocations(row["location"], o.PreserveLocation)
		if err != nil {
			return InlineInventory{}, err
		}
		if len(locations) == 0 {
			locations, err = inlineLocations(defaults["locations"], false)
			if err != nil {
				return InlineInventory{}, err
			}
		}
		for position := 0; position < o.Positions; position++ {
			if len(result.Jobs) >= 500 {
				result.Truncated = true
				break
			}
			if position > 0 {
				if id != "" {
					sourceURL, err = InlineIdentityURL(boardURL, fmt.Sprintf("%s-%d", id, position+1))
				} else {
					sourceURL, err = InlineSyntheticURL(boardURL, title, seen, nil)
				}
				if err != nil {
					return InlineInventory{}, err
				}
			}
			job := Job{URL: sourceURL, Title: title, Description: inlineFallback(description, defaults["description"]), Locations: locations, EmploymentType: inlineFallback(row["employment_type"], defaults["employment_type"]), JobLocationType: inlineFallback(row["job_location_type"], defaults["job_location_type"]), DatePosted: inlineFallback(row["date_posted"], defaults["date_posted"])}
			if !deadline.IsZero() {
				job.Extras = map[string]any{"valid_through": deadline.Format("2006-01-02")}
			}
			result.Jobs = append(result.Jobs, job)
		}
		if result.Truncated {
			break
		}
	}
	if o.SourceSelector != "" && processed != len(ids) || o.SourceURLSelector != "" && processed != len(urls) {
		return InlineInventory{}, ErrInventory
	}
	if len(result.Jobs) == 0 {
		if authoritativeEmpty {
			return InlineInventory{Jobs: []Job{}}, nil
		}
		if o.EmptyText != "" || o.RequireZeroProof {
			return InlineInventory{}, ErrInventory
		}
	}
	result.Truncated = result.Truncated || truncated
	if !result.Truncated && len(result.Jobs) == 0 && expired > 0 && expired == processed*o.Positions {
		result.VerifiedEmptyReason = "all extracted jobs are past their verified deadline"
	}
	return result, nil
}
