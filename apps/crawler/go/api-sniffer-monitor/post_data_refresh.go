package apisniffer

import (
	"fmt"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Session tokens remain operation-local response data, never profile identity.
type postDataRefresh struct {
	source string
	fields []postRefreshField
}
type postRefreshField struct {
	name    string
	pattern *regexp.Regexp
}

func (*postDataRefresh) String() string   { return "configured POST field refresh" }
func (*postDataRefresh) GoString() string { return "configured POST field refresh" }

func parsePostDataRefresh(raw any, o Options, document *Document) (*postDataRefresh, error) {
	if raw == nil {
		return nil, nil
	}
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, ErrOptions
	}
	if len(m) == 0 {
		return nil, nil
	}
	for key := range m {
		if key != "source_url" && key != "fields" {
			return nil, ErrOptions
		}
	}
	if o.Method != http.MethodPost || o.Body == "" {
		return nil, ErrOptions
	}
	fields, ok := m["fields"].(map[string]any)
	if !ok || len(fields) == 0 || len(fields) > 16 {
		return nil, ErrOptions
	}
	source := o.BoardURL
	if raw, exists := m["source_url"]; exists && raw != nil && raw != "" {
		source, ok = raw.(string)
		if !ok {
			return nil, ErrOptions
		}
	}
	if !validURL(source) || source == o.Endpoint {
		return nil, ErrOptions
	}
	base, err := url.Parse(o.BoardURL)
	if err != nil || !validURL(o.BoardURL) {
		return nil, ErrOptions
	}
	u, _ := url.Parse(source)
	if u.Scheme != base.Scheme || u.Host != base.Host {
		return nil, ErrOptions
	}
	names := append([]string{}, document.order[reflect.ValueOf(fields)]...)
	if len(names) != len(fields) {
		return nil, ErrOptions
	}
	result := &postDataRefresh{source: source}
	for _, name := range names {
		pattern, ok := fields[name].(string)
		if !ok || utf8.RuneCountInString(name) < 1 || utf8.RuneCountInString(name) > 256 || !utf8.ValidString(name) || !utf8.ValidString(pattern) || utf8.RuneCountInString(pattern) < 1 || utf8.RuneCountInString(pattern) > 4096 {
			return nil, ErrOptions
		}
		// Linear-time expressions preserve the supported original regex subset.
		compiled, err := regexp.Compile(pattern)
		if err != nil || compiled.NumSubexp() != 1 {
			return nil, ErrOptions
		}
		if _, err := setBodyParam(o.Body, name, "configured-refresh-probe"); err != nil {
			return nil, ErrOptions
		}
		result.fields = append(result.fields, postRefreshField{name, compiled})
	}
	return result, nil
}

func (o Options) IsPostDataRefreshRequest(r Request) bool {
	return o.PostDataRefresh != nil && r.URL == o.PostDataRefresh.source && r.Method == http.MethodGet && r.Body == "" && len(r.Headers) == 0 && !r.Probe
}

func (o Options) refreshedPostData(source string) (Options, error) {
	if o.PostDataRefresh == nil {
		return o, ErrOptions
	}
	if len(source) == 0 || len(source) > 2000000 || !utf8.ValidString(source) {
		return o, ErrInventory
	}
	body := o.Body
	for _, field := range o.PostDataRefresh.fields {
		match := field.pattern.FindStringSubmatch(source)
		if len(match) != 2 || utf8.RuneCountInString(match[1]) < 1 || utf8.RuneCountInString(match[1]) > 16384 {
			return o, ErrInventory
		}
		updated, err := setBodyParam(body, field.name, match[1])
		if err != nil || updated == body {
			return o, ErrInventory
		}
		body = updated
	}
	o.Body = body
	return o, nil
}

// Keep form-field order and repeated names, matching original parse_qsl/urlencode.
func setOrderedFormParam(body, param string, value any) (string, error) {
	parts := strings.Split(body, "&")
	if len(parts) > 1024 {
		return "", ErrOptions
	}
	result := []string{}
	found := false
	for _, part := range parts {
		if part == "" {
			continue
		}
		key, valuePart, _ := strings.Cut(part, "=")
		name, err := url.QueryUnescape(key)
		if err != nil {
			return "", ErrOptions
		}
		old, err := url.QueryUnescape(valuePart)
		if err != nil {
			return "", ErrOptions
		}
		if name == param {
			old = fmt.Sprint(value)
			found = true
		}
		result = append(result, url.QueryEscape(name)+"="+url.QueryEscape(old))
	}
	if !found {
		return "", ErrOptions
	}
	return strings.Join(result, "&"), nil
}
