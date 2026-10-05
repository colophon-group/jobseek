package apisniffer

// Direct detail APIs reuse the monitor's field language without inheriting its
// pagination or inventory contract. Fetching remains owned by the worker.
import (
	"encoding/json"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

var detailPlaceholder = regexp.MustCompile(`\{[A-Za-z_][A-Za-z0-9_]*\}`)

type HTTPDetailOptions struct {
	Request Request
	Path    string
	Fields  map[string]any
	Auth    *HTTPDetailAuth
}
type HTTPDetailAuth struct {
	Request      Request
	Path         string
	HeaderFields map[string]any
}

func detailText(v any) bool {
	s, ok := v.(string)
	return ok && len(s) <= 1<<20 && utf8.ValidString(s) && !strings.ContainsRune(s, 0)
}
func detailHeaders(v any) (http.Header, error) {
	h := http.Header{}
	if v == nil {
		return h, nil
	}
	m, ok := v.(map[string]any)
	if !ok || len(m) > 128 {
		return nil, ErrOptions
	}
	for k, v := range m {
		if !detailText(v) || strings.ContainsAny(k+v.(string), "\r\n") {
			return nil, ErrOptions
		}
		switch strings.ToLower(k) {
		case "host", "connection", "content-length", "accept-encoding", "transfer-encoding":
			continue
		}
		if !validHeaderName(k) {
			return nil, ErrOptions
		}
		h.Set(k, v.(string))
	}
	return h, nil
}
func detailRequest(c map[string]any) (Request, error) {
	r := Request{Method: "GET"}
	for _, k := range []string{"api_url", "method", "post_body", "post_data", "json_path", "url_pattern"} {
		if v := c[k]; v != nil && !detailText(v) {
			return r, ErrOptions
		}
	}
	r.URL, _ = c["api_url"].(string)
	if v, ok := c["method"].(string); ok {
		r.Method = v
	}
	if r.Method != "GET" && r.Method != "POST" {
		return r, ErrOptions
	}
	r.Body, _ = c["post_body"].(string)
	if r.Body == "" {
		r.Body, _ = c["post_data"].(string)
	}
	h := c["request_headers"]
	if h != nil {
		if _, ok := h.(map[string]any); !ok {
			return r, ErrOptions
		}
	}
	if h == nil || len(asEmbeddedObject(h)) == 0 {
		h = c["headers"]
	}
	var err error
	r.Headers, err = detailHeaders(h)
	return r, err
}
func ValidateHTTPDetail(c map[string]any) error {
	for k := range c {
		switch k {
		case "api_url", "method", "post_body", "post_data", "request_headers", "headers", "json_path", "url_pattern", "fields", "auth_request", "enrich":
		default:
			return ErrOptions
		}
	}
	r, err := detailRequest(c)
	if err != nil || !validURL(detailPlaceholder.ReplaceAllString(r.URL, "1")) {
		return ErrOptions
	}
	if p, _ := c["json_path"].(string); p != "" {
		if _, err := Search(map[string]any{}, p); err != nil {
			return ErrOptions
		}
	}
	if p, _ := c["url_pattern"].(string); p != "" {
		if _, err := dom.CompileURLPattern(p); err != nil {
			return ErrOptions
		}
	}
	fields, ok := c["fields"].(map[string]any)
	if !ok || len(fields) == 0 || len(fields) > 128 {
		return ErrOptions
	}
	for k, v := range fields {
		if k == "" || len(k) > 256 || !utf8.ValidString(k) || strings.ContainsRune(k, 0) || ValidateField(v) != nil {
			return ErrOptions
		}
	}
	if v := c["enrich"]; v != nil {
		selected, ok := v.([]any)
		if !ok {
			return ErrOptions
		}
		seen := map[string]bool{}
		for _, v := range selected {
			s, ok := v.(string)
			if !ok || seen[s] {
				return ErrOptions
			}
			switch s {
			case "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary":
			default:
				return ErrOptions
			}
			seen[s] = true
		}
	}
	if v := c["auth_request"]; v != nil {
		a, ok := v.(map[string]any)
		if !ok {
			return ErrOptions
		}
		for k := range a {
			switch k {
			case "api_url", "method", "request_headers", "post_body", "post_data", "json_path", "header_fields":
			default:
				return ErrOptions
			}
		}
		ar, err := detailRequest(a)
		if err != nil || !validURL(ar.URL) {
			return ErrOptions
		}
		if p, _ := a["json_path"].(string); p != "" {
			if _, err := Search(map[string]any{}, p); err != nil {
				return ErrOptions
			}
		}
		fields, ok := a["header_fields"].(map[string]any)
		if !ok || len(fields) == 0 || len(fields) > 32 {
			return ErrOptions
		}
		for k, v := range fields {
			if !validHeaderName(k) || ValidateField(v) != nil {
				return ErrOptions
			}
		}
	}
	return nil
}

func HTTPDetailOptionsForSource(c map[string]any, source string) (HTTPDetailOptions, error) {
	o := HTTPDetailOptions{}
	if ValidateHTTPDetail(c) != nil {
		return o, ErrOptions
	}
	u, err := url.Parse(source)
	if err != nil {
		return o, ErrOptions
	}
	values := map[string]string{}
	if pattern, _ := c["url_pattern"].(string); pattern != "" {
		re, err := dom.CompileURLPattern(pattern)
		if err != nil {
			return o, ErrOptions
		}
		m, err := re.FindStringMatch(source)
		if err != nil {
			return o, ErrOptions
		}
		if m != nil {
			for _, g := range m.Groups() {
				if _, err := strconv.Atoi(g.Name); err != nil && len(g.Captures) > 0 {
					values[g.Name] = g.String()
				}
			}
		}
	}
	if _, ok := values["id"]; !ok {
		path := strings.TrimRight(u.EscapedPath(), "/")
		values["id"] = path[strings.LastIndex(path, "/")+1:]
	}
	o.Request, err = detailRequest(c)
	if err != nil {
		return o, err
	}
	for k, v := range values {
		o.Request.URL = strings.ReplaceAll(o.Request.URL, "{"+k+"}", v)
		o.Request.Body = strings.ReplaceAll(o.Request.Body, "{"+k+"}", v)
	}
	if detailPlaceholder.MatchString(o.Request.URL) || !validURL(o.Request.URL) {
		return o, ErrOptions
	}
	o.Path, _ = c["json_path"].(string)
	o.Fields = c["fields"].(map[string]any)
	if a, ok := c["auth_request"].(map[string]any); ok {
		ar, _ := detailRequest(a)
		p, _ := a["json_path"].(string)
		o.Auth = &HTTPDetailAuth{ar, p, a["header_fields"].(map[string]any)}
	}
	return o, nil
}

func HTTPDetailAuthHeaders(d *Document, a *HTTPDetailAuth) (http.Header, error) {
	item, err := Search(d.Value, a.Path)
	if err != nil {
		return nil, err
	}
	if _, ok := item.(map[string]any); !ok {
		return nil, ErrOptions
	}
	d.Root = map[string]any{}
	h := http.Header{}
	for k, spec := range a.HeaderFields {
		v, err := d.Field(item, spec)
		if err != nil || v == nil {
			return nil, ErrOptions
		}
		if _, ok := v.([]any); ok {
			return nil, ErrOptions
		}
		s, err := d.String(v)
		if err != nil || strings.ContainsAny(s, "\r\n\x00") {
			return nil, ErrOptions
		}
		h.Set(k, s)
	}
	return h, nil
}
func ProjectHTTPDetail(d *Document, o HTTPDetailOptions) (map[string]any, error) {
	item, err := Search(d.Value, o.Path)
	if err != nil {
		return nil, err
	}
	if _, ok := item.(map[string]any); !ok {
		return map[string]any{}, nil
	}
	d.Root = map[string]any{}
	values, metadata, extras := map[string]any{}, map[string]any{}, map[string]any{}
	for k, spec := range o.Fields {
		v, err := d.Field(item, spec)
		if err != nil {
			return nil, err
		}
		if v == nil {
			continue
		}
		switch k {
		case "title", "description", "employment_type", "job_location_type", "date_posted", "base_salary":
			values[k] = v
		case "locations":
			if _, ok := v.([]any); !ok {
				v = []any{v}
			}
			values[k] = v
		case "skills", "responsibilities", "qualifications":
			list, ok := v.([]any)
			if !ok {
				list = []any{v}
			}
			kept := []any{}
			for _, x := range list {
				if !detailTruthy(x) {
					continue
				}
				if s, ok := x.(string); ok && strings.TrimSpace(s) == "" {
					continue
				}
				kept = append(kept, x)
			}
			if len(kept) > 0 {
				extras[k] = kept
			}
		case "valid_through":
			extras[k] = v
		default:
			metadata[strings.TrimPrefix(k, "metadata.")] = v
		}
	}
	if len(metadata) > 0 {
		values["metadata"] = metadata
	}
	if len(extras) > 0 {
		values["extras"] = extras
	}
	return values, nil
}

func detailTruthy(v any) bool {
	if v == nil {
		return false
	}
	switch x := v.(type) {
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		f, _ := x.Float64()
		return f != 0
	}
	r := reflect.ValueOf(v)
	switch r.Kind() {
	case reflect.Map, reflect.Slice:
		return r.Len() > 0
	}
	return true
}
