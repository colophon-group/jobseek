package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/net/html"
)

type TenthProviderFetch func(context.Context, Request) ([]byte, error)

var ErrInterviewebSnapshotChanged = errors.New("Intervieweb pagination snapshot changed")

type InterviewebPage struct {
	URLs, Orders []string
	Endpoint     string
	Pages        int
}

func InterviewebJobURL(raw, board string) (string, bool) {
	absolute, err := PythonJoinURL(board, raw)
	parsed, parsedOK := ParsePythonURL(absolute)
	u, parseErr := url.Parse(parsed.Scheme + "://" + parsed.Host + "/")
	b, boardErr := url.Parse(board)
	if err != nil || !parsedOK || parseErr != nil || boardErr != nil || u.Scheme != "https" || b.Scheme != "https" || !strings.EqualFold(u.Hostname(), b.Hostname()) || u.User != nil || u.Port() != "" && u.Port() != "443" {
		return "", false
	}
	parts := strings.FieldsFunc(parsed.Path, func(r rune) bool { return r == '/' })
	if len(parts) != 3 || !strings.EqualFold(parts[0], "jobs") || len(parts[2]) != 2 || !asciiLetters(parts[2]) {
		return "", false
	}
	return "https://" + strings.ToLower(u.Hostname()) + "/jobs/" + parts[1] + "/" + strings.ToLower(parts[2]) + "/", true
}

func asciiLetters(s string) bool {
	for _, r := range s {
		if r < 'a' || r > 'z' {
			if r < 'A' || r > 'Z' {
				return false
			}
		}
	}
	return true
}

func ParseInterviewebPage(body, board string) (InterviewebPage, error) {
	p := InterviewebPage{URLs: []string{}, Orders: []string{}, Pages: 1}
	classification, err := dom.ClassifyDocument(body, dom.Object{}, board)
	if err != nil || classification["classification"] == "challenge" {
		return p, ErrInventory
	}
	links, orders := map[string]bool{}, map[string]bool{}
	tokens := html.NewTokenizer(strings.NewReader(body))
	for {
		kind := tokens.Next()
		if kind == html.ErrorToken {
			if tokens.Err() != io.EOF {
				return p, ErrInventory
			}
			break
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		t := tokens.Token()
		attrs := map[string]string{}
		for _, a := range t.Attr {
			attrs[a.Key] = a.Val
		}
		if t.Data == "input" && attrs["id"] == "url-for-announces" {
			p.Endpoint = attrs["value"]
		}
		if t.Data == "a" {
			if raw := attrs["href"]; raw != "" {
				if link, ok := InterviewebJobURL(raw, board); ok {
					links[link] = true
				}
			}
			if order := attrs["data-order"]; order != "" {
				for _, c := range strings.Fields(attrs["class"]) {
					if c == "active" {
						orders[order] = true
					}
				}
			}
		}
	}
	pattern, err := dom.CompileURLPattern(`(?i)\bPage\s+\d+\s+of\s+(\d+)\b`)
	if err != nil {
		return p, err
	}
	match, err := pattern.FindStringMatch(body)
	for match != nil && err == nil {
		n, ok, overflow := talentBrewInteger(match.Groups()[1].String())
		if !ok || overflow || n < 1 || n > 1000 {
			return p, ErrInventory
		}
		p.Pages = max(p.Pages, n)
		match, err = pattern.FindNextMatch(match)
	}
	if err != nil {
		return p, err
	}
	for link := range links {
		p.URLs = append(p.URLs, link)
	}
	for order := range orders {
		p.Orders = append(p.Orders, order)
	}
	sort.Strings(p.URLs)
	sort.Strings(p.Orders)
	return p, nil
}

func InterviewebPaginationProtocol(body string, p InterviewebPage, o TenthProviderOptions) (string, string, string, error) {
	endpoint, err := PythonJoinURL(o.BoardURL, p.Endpoint)
	u, parseErr := url.Parse(endpoint)
	if err != nil || parseErr != nil || p.Endpoint == "" || !o.ResourceMatches(endpoint) || len(u.Query()["module"]) != 1 || len(p.Orders) != 1 {
		return "", "", "", ErrInventory
	}
	order := p.Orders[0]
	switch order {
	case "name", "location", "function", "date", "company", "project":
	default:
		return "", "", "", ErrInventory
	}
	pattern, err := dom.CompileURLPattern(`(?i)["']section["']\s*:\s*["']([^"']+)["']`)
	if err != nil {
		return "", "", "", err
	}
	match, err := pattern.FindStringMatch(body)
	if err != nil || match == nil {
		return "", "", "", ErrInventory
	}
	return endpoint, match.Groups()[1].String(), order, nil
}

func InterviewebPageRequest(endpoint, board, section, order string, page int) Request {
	values := url.Values{"act1": {"vacancyListCareer"}, "section": {section}, "order": {order}, "page": {strconv.Itoa(page)}, "country": {""}, "region": {""}, "function": {""}, "project": {""}, "text": {""}, "division": {""}, "company": {""}}
	return Request{Method: "POST", URL: endpoint, Body: values.Encode(), Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded; charset=UTF-8"}, "Referer": {board}, "X-Requested-With": {"XMLHttpRequest"}}}
}

func DiscoverInterviewebSnapshot(ctx context.Context, o TenthProviderOptions, fetch TenthProviderFetch) ([]string, error) {
	raw, err := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if err != nil {
		return nil, err
	}
	body := string(raw)
	if !strings.Contains(body, `id="url-for-announces"`) && !strings.Contains(body, `id='url-for-announces'`) || !strings.Contains(body, "vacancyListCareer") || !strings.Contains(body, "researchAnnounces") {
		return nil, ErrInventory
	}
	first, err := ParseInterviewebPage(body, o.BoardURL)
	if err != nil || len(first.URLs) > 50000 {
		return nil, ErrInventory
	}
	urls := map[string]bool{}
	for _, link := range first.URLs {
		urls[link] = true
	}
	if first.Pages > 1 {
		endpoint, section, order, err := InterviewebPaginationProtocol(body, first, o)
		if err != nil {
			return nil, err
		}
		for page := 2; page <= first.Pages; page++ {
			raw, err := fetch(ctx, InterviewebPageRequest(endpoint, o.BoardURL, section, order, page))
			if err != nil {
				return nil, err
			}
			var payload struct {
				Success bool    `json:"success"`
				Data    *string `json:"data"`
			}
			if json.Unmarshal(raw, &payload) != nil || !payload.Success || payload.Data == nil {
				return nil, ErrInventory
			}
			parsed, err := ParseInterviewebPage(*payload.Data, o.BoardURL)
			if err != nil {
				return nil, err
			}
			if parsed.Pages != first.Pages {
				return nil, ErrInterviewebSnapshotChanged
			}
			if len(parsed.URLs) == 0 {
				return nil, ErrInventory
			}
			added := false
			for _, link := range parsed.URLs {
				if !urls[link] {
					added = true
					urls[link] = true
				}
			}
			if !added {
				return nil, ErrInterviewebSnapshotChanged
			}
			if len(urls) > 50000 {
				return nil, ErrInventory
			}
		}
	}
	result := []string{}
	for link := range urls {
		result = append(result, link)
	}
	sort.Strings(result)
	return result, nil
}
