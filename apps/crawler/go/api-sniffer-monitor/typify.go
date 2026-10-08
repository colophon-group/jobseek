package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"unicode"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type TypifyPageConfig struct {
	APIURL    string
	Functions []string
}

var ErrTypifyPartitionTooLarge = errors.New("Typify function partition exceeds single-response boundary")

func ParseTypifyPageConfig(body string, o TenthProviderOptions) (TypifyPageConfig, error) {
	c := TypifyPageConfig{Functions: []string{}}
	if !strings.Contains(body, "window.typify") {
		return c, ErrInventory
	}
	inputs, err := dom.CompileURLPattern(`(?i)<input\b[^>]*>`)
	if err != nil {
		return c, err
	}
	id, err := dom.CompileURLPattern(`(?i)\bdata-id\s*=\s*['"](?P<id>\d+)['"]`)
	if err != nil {
		return c, err
	}
	seen := map[string]bool{}
	match, err := inputs.FindStringMatch(body)
	for match != nil && err == nil {
		if strings.Contains(match.String(), "cb-function") {
			m, failure := id.FindStringMatch(match.String())
			if failure != nil {
				return c, failure
			}
			if m != nil {
				value := m.GroupByName("id").String()
				if !seen[value] {
					seen[value] = true
					c.Functions = append(c.Functions, value)
				}
			}
		}
		match, err = inputs.FindNextMatch(match)
	}
	if err != nil || len(c.Functions) == 0 || len(c.Functions) > 100 {
		return c, ErrInventory
	}
	lang, err := dom.CompileURLPattern(`(?is)window\.typify\s*=\s*\{.*?\blanguage\s*:\s*['"](?P<language>[a-z]{0,8})['"]`)
	if err != nil {
		return c, err
	}
	value, err := lang.FindStringMatch(body)
	if err != nil {
		return c, err
	}
	language := ""
	if value != nil {
		language = strings.ToLower(value.GroupByName("language").String())
	}
	prefix := ""
	if language != "" && language != "nl" {
		prefix = "/" + language
	}
	u, _ := url.Parse(o.BoardURL)
	host := strings.ToLower(u.Hostname())
	if prefix == "" && (host == "dominosjobs.de" || strings.HasSuffix(host, ".dominosjobs.de")) {
		prefix = "/de"
	}
	c.APIURL = "https://" + u.Host + prefix + "/api/vacancies"
	if o.ConfiguredAPI != "" && strings.TrimRight(o.ConfiguredAPI, "/") != c.APIURL {
		return c, ErrInventory
	}
	return c, nil
}

func TypifyRequest(apiURL, board string, functions []string, mapAll bool) Request {
	flag := "0"
	if mapAll {
		flag = "1"
	}
	values := url.Values{"type": {"vacancy"}, "location": {""}, "km": {"10"}, "lat": {""}, "lng": {""}, "page": {"1"}, "map": {flag}, "collection_id": {""}}
	if len(functions) > 0 {
		values["field_function[]"] = functions
	}
	return Request{Method: "POST", URL: apiURL, Body: values.Encode(), Headers: http.Header{"Accept": {"application/json"}, "Content-Type": {"application/x-www-form-urlencoded; charset=UTF-8"}, "Referer": {board}, "X-Requested-With": {"XMLHttpRequest"}}}
}

func tenthNonnegative(value any) (int, error) {
	var raw string
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return 0, ErrInventory
		}
		raw = string(v)
	case string:
		if v == "" {
			return 0, ErrInventory
		}
		for _, r := range v {
			if !unicode.Is(unicode.Nd, r) {
				return 0, ErrInventory
			}
		}
		raw = v
	default:
		return 0, ErrInventory
	}
	n, ok, overflow := talentBrewInteger(raw)
	if !ok || overflow || n < 0 {
		return 0, ErrInventory
	}
	return n, nil
}

func TypifyTotal(d *Document) (int, error) {
	root, ok := d.Value.(map[string]any)
	if !ok || detailTruthy(root["errors"]) {
		return 0, ErrInventory
	}
	pagination, ok := root["pagination"].(map[string]any)
	if !ok {
		return 0, ErrInventory
	}
	return tenthNonnegative(pagination["total"])
}

func TypifyPartitionRows(d *Document) ([]any, int, error) {
	total, err := TypifyTotal(d)
	if err != nil {
		return nil, 0, err
	}
	root := d.Value.(map[string]any)
	p := root["pagination"].(map[string]any)
	pages, err := tenthNonnegative(p["total_pages"])
	if err != nil {
		return nil, 0, err
	}
	rows, ok := root["results"].([]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	expected := 0
	if total > 0 {
		expected = 1
	}
	if pages > expected {
		return nil, 0, ErrTypifyPartitionTooLarge
	}
	if pages != expected || len(rows) != total {
		return nil, 0, ErrInventory
	}
	return rows, total, nil
}

func TypifyJobFields(row any, board string) (map[string]any, error) {
	m, ok := row.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	title, _ := m["title"].(string)
	raw, ok := m["url"].(string)
	title = strings.TrimSpace(title)
	if title == "" || !ok {
		return nil, ErrInventory
	}
	source, err := PythonJoinURL(board, strings.TrimSpace(raw))
	p, parsedOK := ParsePythonURL(source)
	u, urlErr := url.Parse(p.Scheme + "://" + p.Host + "/")
	b, _ := url.Parse(board)
	if err != nil || !parsedOK || urlErr != nil || u.Scheme != "http" && u.Scheme != "https" || !strings.EqualFold(u.Hostname(), b.Hostname()) {
		return nil, ErrInventory
	}
	var locations []string
	if location, ok := m["location"].(map[string]any); ok {
		if label, ok := location["label"].(string); ok && strings.TrimSpace(label) != "" {
			locations = []string{strings.TrimSpace(label)}
		}
	}
	return map[string]any{"url": source, "title": title, "locations": locations}, nil
}

func DiscoverTypify(ctx context.Context, o TenthProviderOptions, fetch TenthProviderFetch) ([]map[string]any, bool, error) {
	body, err := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if err != nil {
		return nil, false, err
	}
	c, err := ParseTypifyPageConfig(string(body), o)
	if err != nil {
		return nil, false, err
	}
	request := func(ids []string, mapAll bool) (*Document, error) {
		body, err := fetch(ctx, TypifyRequest(c.APIURL, o.BoardURL, ids, mapAll))
		if err != nil {
			return nil, err
		}
		d, err := Decode(body)
		if err != nil {
			return nil, err
		}
		if _, err := TypifyTotal(d); err != nil {
			return nil, err
		}
		return d, nil
	}
	summary, err := request(nil, false)
	if err != nil {
		return nil, false, err
	}
	expected, err := TypifyTotal(summary)
	if err != nil {
		return nil, false, err
	}
	jobs := map[string]map[string]any{}
	order := []string{}
	partitionRows := 0
	requests := 0
	add := func(rows []any) error {
		for _, raw := range rows {
			job, err := TypifyJobFields(raw, o.BoardURL)
			if err != nil {
				return err
			}
			source := job["url"].(string)
			if old, found := jobs[source]; found {
				if !reflect.DeepEqual(old, job) {
					return ErrInventory
				}
				continue
			}
			jobs[source] = job
			order = append(order, source)
		}
		return nil
	}
	var group func([]string) error
	group = func(ids []string) error {
		requests++
		if requests > 200 {
			return ErrInventory
		}
		d, err := request(ids, true)
		if err != nil {
			return err
		}
		rows, total, err := TypifyPartitionRows(d)
		if errors.Is(err, ErrTypifyPartitionTooLarge) && len(ids) > 1 {
			mid := len(ids) / 2
			if err := group(ids[:mid]); err != nil {
				return err
			}
			return group(ids[mid:])
		}
		if err != nil {
			return err
		}
		partitionRows += total
		return add(rows)
	}
	if expected <= 2001 {
		d, err := request(nil, true)
		if err != nil {
			return nil, false, err
		}
		rows, total, err := TypifyPartitionRows(d)
		if err != nil || total != expected {
			return nil, false, ErrInventory
		}
		partitionRows = total
		if err := add(rows); err != nil {
			return nil, false, err
		}
	} else {
		if len(c.Functions) == 1 {
			err = group(c.Functions)
		} else {
			mid := len(c.Functions) / 2
			err = group(c.Functions[:mid])
			if err == nil {
				err = group(c.Functions[mid:])
			}
		}
		if err != nil {
			return nil, false, err
		}
	}
	if partitionRows != expected {
		return nil, false, ErrInventory
	}
	// Stable presentation order does not change source fields or inventory identity.
	sort.Strings(order)
	out := []map[string]any{}
	for _, source := range order {
		out = append(out, jobs[source])
	}
	return out, expected > 50000, nil
}
