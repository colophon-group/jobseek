package apisniffer

import (
	"context"
	stdhtml "html"
	"io"
	"net/http"
	"regexp"
	"strings"

	"golang.org/x/net/html"
)

// UmantisPageFetch owns status/transport retries, reservation checks and an
// isolated same-origin cookie jar. A nil body denotes a terminal 404/410 tail.
// Redirects are checked before following, including vacancy owner documents.
type UmantisPageFetch func(context.Context, string, bool) ([]byte, string, error)

func umantisVisibleEmpty(body, expected string) bool {
	type element struct {
		tag    string
		hidden bool
	}
	stack := []element{}
	depth := 0
	parts := []string{}
	z := html.NewTokenizer(strings.NewReader(body))
	for {
		kind := z.Next()
		if kind == html.ErrorToken {
			break
		}
		t := z.Token()
		if kind == html.TextToken && depth == 0 {
			parts = append(parts, t.Data)
		}
		if kind == html.EndTagToken {
			for i := len(stack) - 1; i >= 0; i-- {
				if stack[i].tag == t.Data {
					for _, e := range stack[i:] {
						if e.hidden {
							depth--
						}
					}
					stack = stack[:i]
					break
				}
			}
		}
		if (kind != html.StartTagToken && kind != html.SelfClosingTagToken) || umantisVoid[t.Data] {
			continue
		}
		a := map[string]string{}
		for _, v := range t.Attr {
			a[v.Key] = v.Val
		}
		_, hidden := a["hidden"]
		style := strings.Join(strings.Fields(umantisIdentity(a["style"])), "")
		hidden = hidden || umantisIdentity(a["aria-hidden"]) == "true" || strings.Contains(style, "display:none") || strings.Contains(style, "visibility:hidden")
		for _, c := range strings.Fields(umantisIdentity(a["class"])) {
			if c == "d-none" || c == "hidden" || c == "sr-only" || c == "visually-hidden" {
				hidden = true
			}
		}
		switch t.Data {
		case "head", "title", "script", "style", "template", "noscript":
			hidden = true
		case "details":
			_, open := a["open"]
			hidden = hidden || !open
		}
		if kind == html.SelfClosingTagToken {
			continue
		}
		stack = append(stack, element{t.Data, hidden})
		if hidden {
			depth++
		}
	}
	return strings.Contains(umantisIdentity(strings.Join(parts, " ")), umantisIdentity(expected))
}

var umantisOwnerSeparator = regexp.MustCompile(`\s+[-–—]\s+`)

// Token-level structure matches the reference parser. Repaired DOM nodes must
// not legitimize a missing head, body-level metadata or a duplicated owner.
func ValidateUmantisDetailOwner(body, employer string) error {
	headCount, headDepth, bodyDepth := 0, 0, 0
	headClosed, bodySeen, bodyClosed, invalid := false, false, false, false
	owners := []string{}
	z := html.NewTokenizer(strings.NewReader(body))
	for {
		kind := z.Next()
		t := z.Token()
		if kind == html.ErrorToken {
			if z.Err() != io.EOF {
				return ErrInventory
			}
			break
		}
		if kind == html.StartTagToken || kind == html.SelfClosingTagToken {
			switch t.Data {
			case "head":
				headCount++
				invalid = invalid || headDepth != 0 || headClosed || bodySeen || bodyDepth != 0
				headDepth++
			case "body":
				invalid = invalid || headCount != 1 || !headClosed || headDepth != 0 || bodySeen || bodyDepth != 0
				bodySeen = true
				bodyDepth++
			case "meta":
				a := map[string]string{}
				for _, v := range t.Attr {
					a[v.Key] = v.Val
				}
				if umantisIdentity(a["name"]) == "description" {
					content, exists := a["content"]
					if headDepth != 1 || bodyDepth != 0 || !exists {
						invalid = true
					} else {
						owners = append(owners, umantisIdentity(umantisOwnerSeparator.Split(content, 2)[0]))
					}
				}
			}
		}
		if kind == html.EndTagToken || kind == html.SelfClosingTagToken {
			switch t.Data {
			case "head":
				invalid = invalid || headDepth != 1 || bodyDepth != 0 || bodySeen
				if headDepth > 0 {
					headDepth--
					if headDepth == 0 {
						headClosed = true
					}
				}
			case "body":
				invalid = invalid || bodyDepth != 1 || !bodySeen || !headClosed
				if bodyDepth > 0 {
					bodyDepth--
					if bodyDepth == 0 {
						bodyClosed = true
					}
				}
			}
		}
	}
	if invalid || headCount != 1 || !headClosed || headDepth != 0 || bodyDepth != 0 || bodySeen && !bodyClosed || len(owners) != 1 || owners[0] != umantisIdentity(employer) {
		return ErrInventory
	}
	return nil
}

func umantisValidateRange(n *UmantisNavigation, rows []UmantisRow, page, first, total int) ([]UmantisRow, error) {
	if n == nil || n.Page != page {
		return nil, ErrInventory
	}
	if n.Total == 0 {
		if n.First != 0 || n.Last != 0 || len(rows) != 0 || page != 1 {
			return nil, ErrInventory
		}
		return []UmantisRow{}, nil
	}
	if total >= 0 && n.Total != total || n.First != first || n.First < 1 || n.Last < n.First || n.Last > n.Total {
		return nil, ErrInventory
	}
	unique, err := DeduplicateUmantisRows(rows)
	if err != nil || len(unique) != n.Last-n.First+1 {
		return nil, ErrInventory
	}
	return unique, nil
}

// DiscoverUmantis returns no successful prefix after a failed advertised page
// or owner document. The boolean reports the legacy reference's truncation.
func DiscoverUmantis(ctx context.Context, o UmantisOptions, fetch UmantisPageFetch) ([]UmantisRow, bool, error) {
	get := func(resource string, tail bool) ([]byte, string, error) {
		if err := ctx.Err(); err != nil {
			return nil, "", err
		}
		if !o.ResourceMatches(resource) {
			return nil, "", ErrInventory
		}
		body, finalURL, err := fetch(ctx, resource, tail)
		if err == nil && body != nil && !o.ResourceMatches(finalURL) {
			return nil, "", ErrInventory
		}
		return body, finalURL, err
	}
	body, currentURL, err := get(o.Listing, false)
	if err != nil {
		return nil, false, err
	}
	if body == nil {
		return nil, false, ErrInventory
	}
	rows, err := ParseUmantisRows(string(body), o)
	if err != nil {
		return nil, false, err
	}
	if o.Strict {
		n, err := ParseUmantisNavigation(string(body))
		if err != nil || n == nil {
			return nil, false, ErrInventory
		}
		first := 1
		if n.Total == 0 {
			first = 0
		}
		rows, err = umantisValidateRange(n, rows, 1, first, -1)
		if err != nil {
			return nil, false, err
		}
		if n.Total == 0 && !umantisVisibleEmpty(string(body), o.EmptyText) {
			return nil, false, ErrInventory
		}
		current := currentURL
		seen := map[string]bool{}
		for _, row := range rows {
			seen[row.ID] = true
		}
		for n.Last < n.Total {
			if n.Page >= 100 {
				return nil, false, ErrInventory
			}
			next, err := o.NextURL(*n, current)
			if err != nil {
				return nil, false, err
			}
			body, _, err = get(next, true)
			if err != nil {
				return nil, false, err
			}
			if body == nil {
				return nil, false, ErrInventory
			}
			page, err := ParseUmantisRows(string(body), o)
			if err != nil {
				return nil, false, err
			}
			nav, err := ParseUmantisNavigation(string(body))
			if err != nil {
				return nil, false, err
			}
			page, err = umantisValidateRange(nav, page, n.Page+1, n.Last+1, n.Total)
			if err != nil {
				return nil, false, err
			}
			for _, row := range page {
				if seen[row.ID] {
					return nil, false, ErrInventory
				}
				seen[row.ID] = true
			}
			rows = append(rows, page...)
			current = next
			n = nav
		}
		if len(rows) != n.Total {
			return nil, false, ErrInventory
		}
		for _, row := range rows {
			body, _, err = get(row.URL, true)
			if err != nil {
				return nil, false, err
			}
			if body == nil || ValidateUmantisDetailOwner(string(body), o.Employer) != nil {
				return nil, false, ErrInventory
			}
		}
		return rows, false, nil
	}
	// The tolerant listings need only the table identity, rather than strict
	// navigation evidence. It is derived from the same table marker as Python.
	table := umantisLegacyTable(string(body))
	truncated := false
	if table != "" {
		for page := 2; ; page++ {
			if page > 100 || len(rows) >= 50000 {
				truncated = true
				break
			}
			next, err := o.PaginationURL(table, page)
			if err != nil {
				return nil, false, err
			}
			body, _, err = get(next, true)
			if err != nil {
				return nil, false, err
			}
			if body == nil {
				break
			}
			tail, err := ParseUmantisRows(string(body), o)
			if err != nil {
				return nil, false, err
			}
			if len(tail) == 0 {
				break
			}
			seen := map[string]bool{}
			for _, row := range rows {
				seen[row.ID] = true
			}
			fresh := false
			for _, row := range tail {
				fresh = fresh || !seen[row.ID]
			}
			if !fresh {
				break
			}
			rows = append(rows, tail...)
		}
	}
	rows, err = DeduplicateUmantisRows(rows)
	if err != nil {
		return nil, false, err
	}
	return rows, truncated, nil
}

// Request construction remains shared with the sealed worker transport.
func UmantisRequest(resource string) Request {
	return Request{Method: "GET", URL: resource, Headers: http.Header{"Accept": {"text/html,application/xhtml+xml"}}}
}

var umantisLegacyTableJSON = regexp.MustCompile(`"TableNr"\s*:\s*"([0-9]+)"`)
var umantisLegacyTableQuery = regexp.MustCompile(`tc([0-9]+)=p[0-9]+`)

func umantisLegacyTable(body string) string {
	if n, err := ParseUmantisNavigation(body); err == nil && n != nil {
		return n.Table
	}
	body = stdhtml.UnescapeString(body)
	for _, pattern := range []*regexp.Regexp{umantisLegacyTableJSON, umantisLegacyTableQuery} {
		if match := pattern.FindStringSubmatch(body); match != nil {
			return match[1]
		}
	}
	return ""
}
