package apisniffer

import (
	"context"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

func jobConvoAttribute(n *xhtml.Node, key string) string {
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val
		}
	}
	return ""
}
func jobConvoText(n *xhtml.Node) string {
	var b strings.Builder
	var walk func(*xhtml.Node)
	walk = func(n *xhtml.Node) {
		if n.Type == xhtml.TextNode {
			b.WriteString(n.Data)
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(n)
	return strings.TrimSpace(b.String())
}

var jobConvoTable = cascadia.MustCompile("table#tbl")
var jobConvoPaginator = cascadia.MustCompile("ul.pagination")
var jobConvoActive = cascadia.MustCompile("li.active")
var jobConvoRows = cascadia.MustCompile("tr.joblist")
var jobConvoLinks = cascadia.MustCompile(`a[href*="jobconvo.com/job/"]`)
var jobConvoPages = cascadia.MustCompile("a[href]")

func JobConvoPage(page, source string, o FinalHTTPProviderOptions) (map[string]string, []string, error) {
	if len(page) > 4<<20 || o.Provider != "jobconvo" {
		return nil, nil, ErrInventory
	}
	root, e := xhtml.Parse(strings.NewReader(page))
	if e != nil {
		return nil, nil, ErrInventory
	}
	tables, paginators := cascadia.QueryAll(root, jobConvoTable), cascadia.QueryAll(root, jobConvoPaginator)
	if len(tables) != 1 || len(paginators) != 1 {
		return nil, nil, ErrInventory
	}
	active := cascadia.QueryAll(paginators[0], jobConvoActive)
	if len(active) != 1 {
		return nil, nil, ErrInventory
	}
	base, e := url.Parse(source)
	if e != nil {
		return nil, nil, ErrInventory
	}
	expected := 1
	if values, ok := base.Query()["page"]; ok && len(values) > 0 {
		expected, e = strconv.Atoi(values[0])
		if e != nil {
			return nil, nil, ErrInventory
		}
	}
	number, e := strconv.Atoi(jobConvoText(active[0]))
	if e != nil || number != expected {
		return nil, nil, ErrInventory
	}
	jobs := map[string]string{}
	for _, row := range cascadia.QueryAll(tables[0], jobConvoRows) {
		links := cascadia.QueryAll(row, jobConvoLinks)
		if len(links) != 1 {
			return nil, nil, ErrInventory
		}
		ref, e := url.Parse(jobConvoAttribute(links[0], "href"))
		if e != nil {
			return nil, nil, ErrInventory
		}
		u := base.ResolveReference(ref)
		match := jobConvoJobPath.FindStringSubmatch(u.Path)
		if len(match) != 3 || u.Scheme != "https" || !jobConvoHost(u) || u.User != nil || u.Port() != "" && u.Port() != "443" {
			return nil, nil, ErrInventory
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil {
			return nil, nil, ErrInventory
		}
		for _, p := range q["career_page"] {
			if strings.ToLower(p) != o.CareerPage {
				return nil, nil, ErrInventory
			}
		}
		id := strings.ToLower(match[2])
		canonical := "https://app.jobconvo.com/job/" + match[1] + "/" + id + "/"
		if previous, ok := jobs[id]; ok && previous != canonical {
			return nil, nil, ErrInventory
		}
		jobs[id] = canonical
	}
	pages := []string{}
	seen := map[string]bool{}
	for _, link := range cascadia.QueryAll(paginators[0], jobConvoPages) {
		href := jobConvoAttribute(link, "href")
		if href == "" || href == "#" {
			continue
		}
		u, e := base.Parse(href)
		if e != nil {
			return nil, nil, ErrInventory
		}
		u.Host = strings.ToLower(u.Host)
		u.Fragment, u.RawFragment = "", ""
		match := jobConvoListingPath.FindStringSubmatch(u.Path)
		if len(match) != 4 || strings.ToLower(match[1]) != o.Locale || match[2] != o.Tenant || strings.ToLower(match[3]) != o.CareerPage || !o.ResourceMatches(u.String()) {
			return nil, nil, ErrInventory
		}
		if !seen[u.String()] {
			pages = append(pages, u.String())
			seen[u.String()] = true
		}
	}
	sort.Strings(pages)
	return jobs, pages, nil
}

func DiscoverJobConvo(ctx context.Context, o FinalHTTPProviderOptions, fetch SmallProviderFetch) ([]string, error) {
	if ctx == nil || fetch == nil || o.Provider != "jobconvo" {
		return nil, ErrOptions
	}
	pending := []string{o.BoardURL}
	queued := map[string]bool{o.BoardURL: true}
	jobs := map[string]string{}
	for len(pending) > 0 {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		source := pending[0]
		pending = pending[1:]
		body, e := fetch(ctx, Request{Method: "GET", URL: source})
		if e != nil {
			return nil, e
		}
		rows, pages, e := JobConvoPage(string(body), source, o)
		if e != nil {
			return nil, e
		}
		for id, source := range rows {
			if previous, ok := jobs[id]; ok && previous != source {
				return nil, ErrInventory
			}
			jobs[id] = source
		}
		if len(jobs) > 50000 {
			return nil, ErrInventory
		}
		for _, page := range pages {
			if !queued[page] {
				if len(queued) >= 1000 {
					return nil, ErrInventory
				}
				queued[page] = true
				pending = append(pending, page)
			}
		}
	}
	out := []string{}
	for _, source := range jobs {
		out = append(out, source)
	}
	sort.Strings(out)
	return out, nil
}

func JobConvoDetailRequest(source, locale string) (Request, string, error) {
	u, e := url.Parse(source)
	if e != nil || !jobConvoHost(u) || u.Scheme != "https" || u.User != nil || u.Port() != "" && u.Port() != "443" || !jobConvoLocale.MatchString(locale) {
		return Request{}, "", ErrOptions
	}
	match := jobConvoJobPath.FindStringSubmatch(u.Path)
	if len(match) != 3 {
		return Request{}, "", ErrOptions
	}
	id := strings.ToLower(match[2])
	return Request{Method: "GET", URL: "https://app.jobconvo.com/" + strings.ToLower(locale) + "/api/job/" + id + "/" + match[1] + "/", Headers: http.Header{"Accept": []string{"application/json"}}}, id, nil
}
