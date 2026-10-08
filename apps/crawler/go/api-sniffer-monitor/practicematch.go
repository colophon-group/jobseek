package apisniffer

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/net/html"
)

type PracticeMatchOptions struct {
	BoardURL, Endpoint string
	MaxPages           int
}
type PracticeMatchInventory struct {
	URLs      []string
	Truncated bool
}

var practiceJobPath = regexp.MustCompile(`(?i)^/physicians/job-details\.cfm/([\p{Nd}]+)(?:/|$)`)
var practiceLandingPath = regexp.MustCompile(`(?i)^/employer/[^/]+/?$`)
var practiceHidden = map[string]bool{"facilityID": true, "facilityLandingURL": true, "contactID": true, "siteID": true, "oppIDs": true, "hasMap": true, "oppProf": true}

func PracticeMatchOptionsFromMetadata(board, raw string) (PracticeMatchOptions, error) {
	o := PracticeMatchOptions{BoardURL: board, MaxPages: 2000}
	u, err := url.Parse(board)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Port() != "" && u.Port() != "443" || u.Fragment != "" || u.RawQuery != "" || !practiceLandingPath.MatchString(u.Path) || strings.HasSuffix(strings.ToLower(strings.TrimRight(u.Path, "/")), ".cfm") || strings.ToLower(u.Hostname()) != "employer.practicematch.com" && strings.ToLower(u.Hostname()) != "www.practicematch.com" {
		return o, ErrOptions
	}
	md, err := DecodeInlineMetadata(raw)
	if err != nil {
		return o, ErrOptions
	}
	for k := range md {
		switch k {
		case "proxy", "max_pages", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return o, ErrOptions
		}
	}
	if v, exists := md["proxy"]; exists {
		if _, ok := v.(bool); !ok {
			return o, ErrOptions
		}
	}
	if v := md["max_pages"]; v != nil {
		switch n := v.(type) {
		case json.Number:
			o.MaxPages, err = strconv.Atoi(string(n))
		case string:
			o.MaxPages, err = strconv.Atoi(strings.TrimSpace(n))
		default:
			return o, ErrOptions
		}
		if err != nil || o.MaxPages < 1 {
			return o, ErrOptions
		}
		o.MaxPages = min(o.MaxPages, 2000)
	}
	o.Endpoint = u.Scheme + "://" + u.Host + "/employer/cfcs/landingPageUtils.cfc?method=getOppData2"
	return o, nil
}

func (o PracticeMatchOptions) ResourceMatches(resource string) bool {
	return resource == o.BoardURL || resource == o.Endpoint
}

func PracticeMatchCanonicalJobURL(raw string) string {
	parsed, ok := ParsePythonURL(raw)
	u, err := url.Parse("//" + parsed.Host)
	if !ok || err != nil || strings.ToLower(u.Hostname()) != "practicematch.com" && strings.ToLower(u.Hostname()) != "www.practicematch.com" {
		return ""
	}
	id := practiceJobPath.FindStringSubmatch(parsed.Path)
	if id == nil {
		return ""
	}
	return "https://www.practicematch.com/physicians/job-details.cfm/" + id[1] + "/"
}

func ParsePracticeMatchLanding(body []byte) (map[string]string, []string, error) {
	if len(body) > 25_000_000 {
		return nil, nil, ErrInventory
	}
	fields, urls := map[string]string{}, map[string]bool{}
	z := html.NewTokenizer(strings.NewReader(string(body)))
	for {
		switch z.Next() {
		case html.ErrorToken:
			if z.Err() != io.EOF {
				return nil, nil, ErrInventory
			}
			out := []string{}
			for u := range urls {
				out = append(out, u)
			}
			sort.Strings(out)
			return fields, out, nil
		case html.StartTagToken, html.SelfClosingTagToken:
			t := z.Token()
			attrs := map[string]string{}
			for _, a := range t.Attr {
				attrs[a.Key] = a.Val
			}
			if t.Data == "input" && practiceHidden[attrs["id"]] {
				fields[attrs["id"]] = attrs["value"]
			} else if t.Data == "a" {
				if u := PracticeMatchCanonicalJobURL(attrs["href"]); u != "" {
					urls[u] = true
				}
			}
		}
	}
}

func PracticeMatchPageRequest(o PracticeMatchOptions, hidden map[string]string, profession string, page int) (Request, error) {
	if hidden["facilityID"] == "" || profession != "1" && profession != "-1" || page < 1 || page > o.MaxPages {
		return Request{}, ErrOptions
	}
	get := func(k, fallback string) string {
		if v, ok := hidden[k]; ok {
			return v
		}
		return fallback
	}
	pairs := [][2]string{{"facilityID", hidden["facilityID"]}, {"facilityLandingURL", get("facilityLandingURL", "")}, {"specialty", ""}, {"professionID", profession}, {"keywords", ""}, {"pageNum", strconv.Itoa(page)}, {"state", ""}, {"contactID", get("contactID", "0")}, {"siteID", get("siteID", "")}, {"oppIDs", get("oppIDs", "0")}, {"hasMap", "0"}, {"updateSpec", "0"}}
	values := []string{}
	for _, p := range pairs {
		values = append(values, url.QueryEscape(p[0])+"="+url.QueryEscape(p[1]))
	}
	return Request{Method: "POST", URL: o.Endpoint, Body: strings.Join(values, "&"), Headers: http.Header{"Accept": {"application/json, text/javascript, */*; q=0.01"}, "Content-Type": {"application/x-www-form-urlencoded; charset=UTF-8"}, "Referer": {o.BoardURL}, "X-Requested-With": {"XMLHttpRequest"}}}, nil
}

func ParsePracticeMatchPage(body []byte) ([]string, error) {
	doc, err := Decode(body)
	if err != nil {
		return nil, ErrInventory
	}
	data, ok := doc.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	value := data["OPPLISTINGSHTML"]
	if value == nil {
		value = data["oppListingsHTML"]
	}
	text, ok := value.(string)
	if !ok {
		return nil, ErrInventory
	}
	_, urls, err := ParsePracticeMatchLanding([]byte(text))
	return urls, err
}

func DiscoverPracticeMatch(ctx context.Context, o PracticeMatchOptions, fetch func(context.Context, Request) ([]byte, error)) (PracticeMatchInventory, error) {
	fail := func(e error) (PracticeMatchInventory, error) { return PracticeMatchInventory{}, e }
	body, err := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if err != nil {
		return fail(err)
	}
	hidden, initial, err := ParsePracticeMatchLanding(body)
	if err != nil || hidden["facilityID"] == "" {
		return fail(ErrInventory)
	}
	urls := map[string]bool{}
	for _, u := range initial {
		urls[u] = true
	}
	truncated := false
	for _, profession := range []string{"1", "-1"} {
		start := 1
		if profession == "1" && len(initial) > 0 {
			start = 2
		}
		ended := false
		for page := start; page <= o.MaxPages; page++ {
			if ctx.Err() != nil {
				return fail(ctx.Err())
			}
			r, err := PracticeMatchPageRequest(o, hidden, profession, page)
			if err != nil {
				return fail(err)
			}
			body, err := fetch(ctx, r)
			if err != nil {
				return fail(err)
			}
			child, err := ParsePracticeMatchPage(body)
			if err != nil {
				return fail(err)
			}
			added := 0
			for _, u := range child {
				if !urls[u] {
					urls[u], added = true, added+1
				}
			}
			if added == 0 {
				ended = true
				break
			}
			if len(urls) >= 50_000 {
				truncated, ended = true, true
				break
			}
		}
		if !ended {
			truncated = true
		}
		if len(urls) >= 50_000 {
			break
		}
	}
	out := PracticeMatchInventory{URLs: []string{}, Truncated: truncated}
	for u := range urls {
		out.URLs = append(out.URLs, u)
	}
	sort.Strings(out.URLs)
	return out, nil
}
