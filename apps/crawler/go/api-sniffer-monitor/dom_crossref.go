package apisniffer

import (
	"context"
	"net/url"
	"regexp"
	"strings"
)

// DOMLinks is provided only by the controller holding the current browser
// target. It is never selected from metadata or retained by a returned result.
type DOMLinks func(context.Context) ([]string, error)

var domCrossrefID = regexp.MustCompile(`(?i)^(id|positionId|position_id|jobId|job_id|reqId|req_id|requisitionId|posting_id|postingId|externalId)$`)

// Match the original DOM cross-reference: first ID field, first same-host link
// across the first ten IDs, path prefix and optional source slug/query fields.
// The reference must contain the ID in its path; a query-only match grants no
// template. No API endpoint or request credentials become a posting URL.
func DOMCrossrefURLs(document *Document, items []map[string]any, board string, links []string) (string, map[string]string, error) {
	if document == nil {
		return "", nil, ErrInventory
	}
	result := map[string]string{}
	if len(items) == 0 {
		return "", result, nil
	}
	field := ""
	for _, key := range document.ObjectKeys(items[0]) {
		if domCrossrefID.MatchString(key) {
			field = key
			break
		}
	}
	if field == "" {
		return "", result, nil
	}
	ids := []string{}
	for _, item := range items {
		if v, exists := item[field]; exists {
			s, e := document.String(v)
			if e != nil {
				return "", nil, e
			}
			ids = append(ids, s)
		}
	}
	page, e := url.Parse(board)
	if e != nil || page.Host == "" {
		return "", nil, ErrInventory
	}
	var reference *url.URL
	refID := ""
	for _, id := range ids[:min(10, len(ids))] {
		for _, href := range links {
			u, e := url.Parse(href)
			if e == nil && strings.Contains(href, id) && u.Host == page.Host && u.User == nil {
				reference, refID = u, id
				break
			}
		}
		if reference != nil {
			break
		}
	}
	if reference == nil {
		return field, result, nil
	}
	refPath := reference.EscapedPath()
	at := strings.Index(refPath, refID)
	if at < 0 {
		return field, result, nil
	}
	prefix, after := refPath[:at], refPath[at+len(refID):]
	var refItem map[string]any
	for _, item := range items {
		s, e := document.String(item[field])
		if e != nil {
			return "", nil, e
		}
		if s == refID {
			refItem = item
			break
		}
	}
	slugField := ""
	if refItem != nil && strings.HasPrefix(after, "/") {
		for _, key := range document.ObjectKeys(refItem) {
			if value, ok := refItem[key].(string); ok && value != "" && strings.HasPrefix(after[1:], value) {
				slugField = key
				break
			}
		}
	}
	type queryField struct{ key, path string }
	query := []queryField{}
	seen := map[string]bool{}
	for _, part := range strings.Split(reference.RawQuery, "&") {
		k, v, _ := strings.Cut(part, "=")
		k, ke := url.QueryUnescape(k)
		v, ve := url.QueryUnescape(v)
		if ke != nil || ve != nil || v == "" || seen[k] {
			continue
		}
		seen[k] = true
		matched := ""
		for _, key := range document.ObjectKeys(refItem) {
			switch value := refItem[key].(type) {
			case string:
				if value == v {
					matched = key
				}
			case map[string]any:
				for _, nested := range document.ObjectKeys(value) {
					if s, ok := value[nested].(string); ok && s == v {
						matched = key + "." + nested
					}
				}
			}
		}
		if matched != "" {
			query = append(query, queryField{k, matched})
		}
	}
	for _, item := range items {
		value, exists := item[field]
		if !exists {
			value = ""
		}
		id, e := document.String(value)
		if e != nil {
			return "", nil, e
		}
		if id == "" {
			continue
		}
		path := prefix + id
		if slugField != "" {
			v, exists := item[slugField]
			if !exists {
				v = ""
			}
			slug, e := document.String(v)
			if e != nil {
				return "", nil, e
			}
			if slug != "" {
				path += "/" + slug
			}
		}
		full, e := PythonJoinURL(board, path)
		if e != nil {
			return "", nil, e
		}
		pairs := []string{}
		for _, q := range query {
			v, e := Search(item, q.path)
			if e != nil {
				return "", nil, e
			}
			if v != nil {
				s, e := document.String(v)
				if e != nil {
					return "", nil, e
				}
				pairs = append(pairs, url.QueryEscape(q.key)+"="+url.QueryEscape(s))
			}
		}
		if len(pairs) > 0 {
			full += "?" + strings.Join(pairs, "&")
		}
		result[id] = full
	}
	return field, result, nil
}
