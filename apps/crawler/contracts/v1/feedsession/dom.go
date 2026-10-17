package feedsession

import (
	"net/url"
	"strconv"
	"strings"
)

// DOMTraversal keeps the rendered root and later raw HTML fetches on one
// target. Page numbers on the wire are sequential; PageStart is the original
// query value, including the legacy -1 and zero starts.
type DOMTraversal struct {
	Parameter    string `json:"parameter"`
	PageStart    int    `json:"page_start"`
	Increment    int    `json:"increment"`
	Attempts     int    `json:"attempts"`
	Transient403 bool   `json:"transient_403"`
}

func (d DOMTraversal) Valid() bool {
	return parameter.MatchString(d.Parameter) && d.PageStart >= -1 && d.PageStart <= 1000000 && d.Increment >= 1 && d.Increment <= 1000000 && d.Attempts >= 1 && d.Attempts <= 5
}

func (d DOMTraversal) PageURL(root string, page int) (string, error) {
	if !d.Valid() || page < 1 || page > 10000 {
		return "", ErrProtocol
	}
	if page == 1 {
		return root, nil
	}
	u, err := url.Parse(root)
	if err != nil {
		return "", ErrProtocol
	}
	keys := []string{}
	values := map[string][]string{}
	for _, entry := range strings.Split(u.RawQuery, "&") {
		key, value, ok := strings.Cut(entry, "=")
		if !ok || value == "" {
			continue
		}
		key, err = url.QueryUnescape(key)
		if err != nil {
			return "", ErrProtocol
		}
		value, err = url.QueryUnescape(value)
		if err != nil {
			return "", ErrProtocol
		}
		if _, exists := values[key]; !exists {
			keys = append(keys, key)
		}
		values[key] = append(values[key], value)
	}
	if _, exists := values[d.Parameter]; !exists {
		keys = append(keys, d.Parameter)
	}
	values[d.Parameter] = []string{strconv.Itoa(d.PageStart + (page-1)*d.Increment)}
	parts := []string{}
	for _, key := range keys {
		for _, value := range values[key] {
			parts = append(parts, url.QueryEscape(key)+"="+url.QueryEscape(value))
		}
	}
	u.RawQuery = strings.Join(parts, "&")
	return u.String(), nil
}
