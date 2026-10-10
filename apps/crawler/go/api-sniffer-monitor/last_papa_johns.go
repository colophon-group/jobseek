package apisniffer

import (
	"context"
	xhtml "golang.org/x/net/html"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var papaCount = regexp.MustCompile(`(?i)\bfound\s+([0-9][0-9,]*)\s+jobs\s+at\s+papa\s+johns\b`)

type PapaListing struct {
	URLs         []string
	Total, Pages int
}

func ParsePapaListing(source, base string) (PapaListing, error) {
	out := PapaListing{Pages: 1}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return out, e
	}
	text := []string{}
	seen := map[string]bool{}
	var visit func(*xhtml.Node) error
	visit = func(n *xhtml.Node) error {
		if n.Type == xhtml.TextNode {
			s := strings.TrimFunc(n.Data, enterpriseSpace)
			if s != "" {
				text = append(text, s)
			}
		}
		if n.Type == xhtml.ElementNode && n.Data == "a" {
			href := ""
			for _, a := range n.Attr {
				if a.Key == "href" {
					href = a.Val
				}
			}
			b, e := url.Parse(base)
			if e != nil {
				return e
			}
			r, e := url.Parse(href)
			if e == nil && href != "" {
				u := b.ResolveReference(r)
				p := u.Port()
				public := u.Scheme == "https" && strings.EqualFold(u.Hostname(), "jobs.papajohns.com") && u.User == nil && (p == "" || p == "443")
				if public && u.RawQuery == "" && u.Fragment == "" && papaJobPath.MatchString(u.Path) {
					target := "https://jobs.papajohns.com" + strings.TrimRight(u.Path, "/") + "/"
					seen[target] = true
				}
				if public && strings.EqualFold(strings.TrimRight(u.Path, "/"), "/jobs") {
					for _, v := range u.Query()["page_jobs"] {
						number, e := strconv.Atoi(v)
						if e == nil && smallUnsigned.MatchString(v) && number >= 1 && number <= 1000 && number > out.Pages {
							out.Pages = number
						}
					}
				}
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			if e := visit(c); e != nil {
				return e
			}
		}
		return nil
	}
	if e := visit(doc); e != nil {
		return out, e
	}
	match := papaCount.FindStringSubmatch(strings.Join(text, " "))
	if len(match) != 2 {
		return out, ErrInventory
	}
	out.Total, e = strconv.Atoi(strings.ReplaceAll(match[1], ",", ""))
	if e != nil {
		return out, ErrInventory
	}
	for u := range seen {
		out.URLs = append(out.URLs, u)
	}
	sort.Strings(out.URLs)
	if out.Total > 0 && len(out.URLs) == 0 {
		return out, ErrInventory
	}
	return out, nil
}
func DiscoverPapaJohns(ctx context.Context, o LastHTTPOptions, fetch SmallProviderFetch) ([]string, error) {
	if o.Provider != "papa_johns" || fetch == nil {
		return nil, ErrInventory
	}
	page := func(n int) (PapaListing, error) {
		target := o.ListingURL()
		if n > 1 {
			target += "?page_jobs=" + strconv.Itoa(n)
		}
		body, e := fetch(ctx, Request{Method: "GET", URL: target})
		if e != nil {
			return PapaListing{}, e
		}
		source := string(body)
		if len([]rune(source)) > 10_000_000 {
			source = string([]rune(source)[:10_000_000])
		}
		return ParsePapaListing(source, target)
	}
	first, e := page(1)
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	for _, u := range first.URLs {
		seen[u] = true
	}
	for start := 2; start <= first.Pages; start += 4 {
		type result struct {
			p PapaListing
			e error
		}
		count := min(4, first.Pages-start+1)
		slots := make([]chan result, count)
		for n := 0; n < count; n++ {
			slots[n] = make(chan result, 1)
			go func(pageIndex, slot int) { p, e := page(pageIndex); slots[slot] <- result{p, e} }(start+n, n)
		}
		results := make([]result, count)
		for n, c := range slots {
			// The verified fetch honors cancellation. Drain every child before
			// returning so no page operation outlives its inventory attempt.
			results[n] = <-c
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		for _, r := range results {
			if r.e != nil {
				return nil, r.e
			}
			if r.p.Total != first.Total || r.p.Pages != first.Pages {
				return nil, ErrInventory
			}
			for _, u := range r.p.URLs {
				seen[u] = true
			}
		}
	}
	if len(seen) != first.Total {
		return nil, ErrInventory
	}
	out := make([]string, 0, len(seen))
	for u := range seen {
		out = append(out, u)
	}
	sort.Strings(out)
	return out, nil
}
