package apisniffer

import (
	"encoding/json"
	"errors"
	"fmt"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"net/http"
	"net/url"
	"reflect"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/jmespath/go-jmespath"
)

var ErrOptions = errors.New("unsupported configured HTTP API monitor")

type Pagination struct {
	Param, Style, Location, ValueTemplate string
	Start, Increment, MaxPages            int
}

type Options struct {
	BoardURL, Endpoint, Method, Body       string
	Path, TotalPath, URLField, URLTemplate string
	TemplateFields                         map[string]any
	Fields                                 map[string]any
	EmptyResponse                          map[string]any
	ItemFilter                             *ItemFilter
	Headers                                http.Header
	Pagination                             *Pagination
	PathValues                             bool
	AutoPath                               bool
	MaxItems, Attempts                     int
	Transient403                           bool
	Enrichment                             []string
}

var ConfigKeys = []string{"api_url", "method", "json_path", "json_path_values", "total_path", "url_field", "url_template", "url_template_fields", "fields", "params", "post_data", "post_body", "request_headers", "headers", "pagination", "max_items", "transient_403", "transport_attempts", "browser", "render", "proxy", "skip_ssl", "ssl_verify", "wait", "timeout", "settle", "items", "score", "total", "empty_response", "item_filter", "url_filter"}

// Explicit HTTP configurations share the production client and the original
// inventory writer. Browser captures, rotating auth, provider-specific filters
// and transformations retain their owner until their native contract is ported.
func OptionsFromMetadata(boardURL, metadata string) (Options, error) {
	o := Options{BoardURL: boardURL, Method: "GET", MaxItems: 10000, Attempts: 3, Headers: http.Header{}, Fields: map[string]any{}, TemplateFields: map[string]any{}}
	d, err := Decode([]byte(metadata))
	if err != nil {
		return o, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	allowed := map[string]bool{}
	for _, k := range ConfigKeys {
		allowed[k] = true
	}
	for _, k := range []string{"scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor"} {
		allowed[k] = true
	}
	for k := range m {
		if !allowed[k] {
			return o, ErrOptions
		}
	}
	if value, exists := m["empty_response"]; exists && value != nil {
		o.EmptyResponse, err = emptyResponseOptions(value)
		if err != nil {
			return o, err
		}
	}
	if value := m["url_filter"]; value != nil {
		if _, err := dom.ListingOptions(dom.Object{"url_filter": value}, boardURL); err != nil {
			return o, ErrOptions
		}
	}
	o.ItemFilter, err = ItemFilterOptions(m["item_filter"])
	if err != nil {
		return o, err
	}
	if sc, ok := m["scraper_config"].(map[string]any); ok {
		if raw, present := sc["enrich"]; present && raw != nil {
			fields, ok := raw.([]any)
			if !ok {
				return o, ErrOptions
			}
			allowed := map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true}
			seen := map[string]bool{}
			for _, raw := range fields {
				field, ok := raw.(string)
				if !ok || !allowed[field] || seen[field] || m["scraper_type"] == "skip" {
					return o, ErrOptions
				}
				seen[field] = true
				o.Enrichment = append(o.Enrichment, field)
			}
		}
	}
	for _, k := range []string{"browser", "render", "proxy", "skip_ssl"} {
		if v, exists := m[k]; exists && v != nil {
			flag, ok := v.(bool)
			if !ok || flag {
				return o, ErrOptions
			}
		}
	}
	if v, exists := m["ssl_verify"]; exists && v != nil {
		flag, ok := v.(bool)
		if !ok || !flag {
			return o, ErrOptions
		}
	}
	read := func(k string, dst *string) error {
		if v, exists := m[k]; exists && v != nil {
			s, ok := v.(string)
			if !ok || !utf8.ValidString(s) || len(s) > 1<<20 || strings.ContainsRune(s, 0) {
				return ErrOptions
			}
			*dst = s
		}
		return nil
	}
	for k, dst := range map[string]*string{"api_url": &o.Endpoint, "method": &o.Method, "json_path": &o.Path, "total_path": &o.TotalPath, "url_field": &o.URLField, "url_template": &o.URLTemplate} {
		if read(k, dst) != nil {
			return o, ErrOptions
		}
	}
	o.Method = strings.ToUpper(o.Method)
	if o.Method != "GET" && o.Method != "POST" {
		return o, ErrOptions
	}
	if !validURL(boardURL) || !validURL(o.Endpoint) {
		return o, ErrOptions
	}
	if value, exists := m["json_path"]; !exists || value == nil {
		fields, explicitFields := m["fields"].(map[string]any)
		if o.URLField == "" || !explicitFields || len(fields) == 0 {
			return o, ErrOptions
		}
		o.AutoPath = true
	}
	for _, p := range []string{o.Path, o.TotalPath} {
		if p != "" {
			if _, err := jmespath.Compile(p); err != nil {
				return o, ErrOptions
			}
		}
	}
	if v, exists := m["json_path_values"]; exists {
		var ok bool
		o.PathValues, ok = v.(bool)
		if !ok {
			return o, ErrOptions
		}
	}
	if p, exists := m["params"]; exists && p != nil {
		params, ok := p.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		u, _ := url.Parse(o.Endpoint)
		q, err := url.ParseQuery(u.RawQuery)
		if err != nil {
			return o, ErrOptions
		}
		for _, k := range d.order[reflect.ValueOf(params)] {
			v := params[k]
			if a, ok := v.([]any); ok {
				values := []string{}
				for _, x := range a {
					s, err := d.String(x)
					if err != nil {
						return o, ErrOptions
					}
					values = append(values, s)
				}
				q[k] = values
			} else {
				s, err := d.String(v)
				if err != nil {
					return o, ErrOptions
				}
				q.Set(k, s)
			}
		}
		u.RawQuery = q.Encode()
		o.Endpoint = u.String()
	}
	post, exists := m["post_data"]
	if !exists {
		post = m["post_body"]
	}
	if post != nil {
		if s, ok := post.(string); ok {
			o.Body = s
		} else {
			switch post.(type) {
			case map[string]any, []any:
				body, err := d.jsonBody(post, false)
				if err != nil {
					return o, ErrOptions
				}
				o.Body = body
			default:
				return o, ErrOptions
			}
		}
	}
	if len(o.Body) > 8<<20 {
		return o, ErrOptions
	}
	h := m["request_headers"]
	if obj, ok := h.(map[string]any); !ok || len(obj) == 0 {
		h = m["headers"]
	}
	if h != nil {
		obj, ok := h.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		for k, v := range obj {
			s, ok := v.(string)
			if !ok || strings.ContainsAny(k+s, "\r\n\x00") {
				return o, ErrOptions
			}
			switch strings.ToLower(k) {
			case "host", "connection", "content-length", "accept-encoding", "transfer-encoding":
				continue
			}
			if !validHeaderName(k) {
				return o, ErrOptions
			}
			o.Headers.Set(k, s)
		}
	}
	for k, dst := range map[string]*int{"max_items": &o.MaxItems, "transport_attempts": &o.Attempts} {
		if v, exists := m[k]; exists {
			n, ok := integer(v)
			if !ok || n < 1 || k == "max_items" && n > 2000000 || k == "transport_attempts" && n > 5 {
				return o, ErrOptions
			}
			*dst = n
		}
	}
	if v, exists := m["transient_403"]; exists {
		var ok bool
		o.Transient403, ok = v.(bool)
		if !ok {
			return o, ErrOptions
		}
	}
	if !o.Transient403 {
		o.Attempts = 3
	} // Python applies transport_attempts only to this explicit retry route.
	if !o.Transient403 {
		o.Attempts = 3
	}
	for k, dst := range map[string]*map[string]any{"fields": &o.Fields, "url_template_fields": &o.TemplateFields} {
		if v, exists := m[k]; exists && v != nil {
			obj, ok := v.(map[string]any)
			if !ok {
				return o, ErrOptions
			}
			for _, spec := range obj {
				if ValidateField(spec) != nil {
					return o, ErrOptions
				}
			}
			*dst = obj
		}
	}
	if o.URLTemplate == "" && o.URLField == "" {
		return o, ErrOptions
	}
	if len(o.Fields) == 0 {
		return o, ErrOptions
	} // Python auto-maps fields; never silently downgrade to URL-only.
	if o.URLField != "" {
		if _, err := jmespath.Compile(o.URLField); err != nil {
			return o, ErrOptions
		}
	}
	if o.URLTemplate != "" {
		if _, err := formatTemplate(o.URLTemplate, map[string]string{}, false); err != nil {
			return o, ErrOptions
		}
	}
	if raw, exists := m["pagination"]; exists && raw != nil {
		p, ok := raw.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		pg := &Pagination{Style: "page", Location: "query", Increment: 1, MaxPages: 200}
		for k, v := range p {
			switch k {
			case "param_name", "style", "location", "value_template":
				s, ok := v.(string)
				if !ok {
					return o, ErrOptions
				}
				switch k {
				case "param_name":
					pg.Param = s
				case "style":
					pg.Style = s
				case "location":
					pg.Location = s
				case "value_template":
					pg.ValueTemplate = s
				}
			case "start_value", "increment", "max_pages", "page_size":
				n, ok := integer(v)
				if !ok {
					return o, ErrOptions
				}
				switch k {
				case "start_value":
					pg.Start = n
				case "increment":
					pg.Increment = n
				case "max_pages":
					pg.MaxPages = n
				}
			default:
				return o, ErrOptions
			}
		}
		if pg.Param == "" || len(pg.Param) > 256 || pg.Start < 0 || pg.Increment < 1 || pg.Increment > 2000000 || pg.MaxPages < 1 || pg.MaxPages > 500 || pg.Style != "page" && pg.Style != "offset" && pg.Style != "cumulative_limit" || pg.Location != "query" && pg.Location != "body" {
			return o, ErrOptions
		}
		if pg.ValueTemplate != "" {
			if _, err := formatTemplate(pg.ValueTemplate, map[string]string{"value": "0"}, true); err != nil {
				return o, ErrOptions
			}
		}
		if pg.Location == "body" {
			if _, err := setBodyParam(o.Body, pg.Param, 0); err != nil {
				return o, ErrOptions
			}
		}
		o.Pagination = pg
	}
	return o, nil
}

func validURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && len(raw) <= 8192 && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Opaque == "" && u.Fragment == "" && (u.Port() == "" || u.Port() == "443") && !strings.ContainsAny(raw, "\r\n\x00")
}
func validHeaderName(k string) bool {
	if k == "" {
		return false
	}
	for _, r := range k {
		if r > 127 || !strings.ContainsRune("!#$%&'*+-.^_`|~", r) && !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9') {
			return false
		}
	}
	return true
}
func integer(v any) (int, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	i, err := strconv.Atoi(string(n))
	return i, err == nil && strconv.Itoa(i) == string(n)
}

func ValidateField(spec any) error {
	switch v := spec.(type) {
	case string:
		if strings.HasPrefix(v, "=") {
			return nil
		}
		_, err := jmespath.Compile(v)
		return err
	case []any:
		for _, x := range v {
			if o, ok := x.(map[string]any); ok {
				p, ok := o["each"].(string)
				if !ok || p == "" {
					return ErrOptions
				}
				if _, ok := o["wrap"].(string); !ok || len(o) != 2 {
					return ErrOptions
				}
				if _, err := jmespath.Compile(p); err != nil {
					return ErrOptions
				}
			} else if _, ok := x.(string); !ok || ValidateField(x) != nil {
				return ErrOptions
			}
		}
		return nil
	case map[string]any:
		if c, exists := v["concat"]; exists {
			if _, ok := c.([]any); !ok {
				return ErrOptions
			}
			if s, exists := v["separator"]; exists {
				if _, ok := s.(string); !ok {
					return ErrOptions
				}
			}
			if len(v) > 2 {
				return ErrOptions
			}
			return ValidateField(c)
		}
		if p, ok := v["lookup_from"].(string); ok {
			key, ok := v["key_from"].(string)
			if !ok || len(v) != 2 {
				return ErrOptions
			}
			if _, err := jmespath.Compile(p); err != nil {
				return ErrOptions
			}
			_, err := jmespath.Compile(key)
			return err
		}
		p, ok := v["path"].(string)
		if !ok {
			return ErrOptions
		}
		if _, err := jmespath.Compile(p); err != nil {
			return ErrOptions
		}
		for k, x := range v {
			switch k {
			case "path":
			case "map":
				if _, ok := x.(map[string]any); !ok {
					return ErrOptions
				}
			case "html_unescape":
				if _, ok := x.(bool); !ok {
					return ErrOptions
				}
			case "timestamp_unit":
				if x != "seconds" && x != "milliseconds" {
					return ErrOptions
				}
			case "key_pattern":
				s, ok := x.(string)
				if !ok || len(v) != 2 || s == "" || len(s) > 256 {
					return ErrOptions
				}
				if _, err := dom.CompileURLPattern(s); err != nil {
					return ErrOptions
				}
			default:
				return ErrOptions
			}
		}
		return nil
	}
	return ErrOptions
}

func formatTemplate(template string, values map[string]string, require bool) (string, error) {
	var out strings.Builder
	for i := 0; i < len(template); {
		c := template[i]
		if c == '{' || c == '}' {
			if i+1 < len(template) && template[i+1] == c {
				out.WriteByte(c)
				i += 2
				continue
			}
			if c == '}' {
				return "", ErrOptions
			}
			end := strings.IndexByte(template[i+1:], '}')
			if end < 0 {
				return "", ErrOptions
			}
			key := template[i+1 : i+1+end]
			if key == "" || strings.ContainsAny(key, "{}!:[].") {
				return "", ErrOptions
			}
			v, exists := values[key]
			if require && !exists {
				return "", ErrField
			}
			out.WriteString(v)
			i += end + 2
		} else {
			out.WriteByte(c)
			i++
		}
	}
	return out.String(), nil
}

func setBodyParam(body, param string, value any) (string, error) {
	d, err := Decode([]byte(body))
	if err == nil {
		obj, ok := d.Value.(map[string]any)
		if !ok {
			return "", ErrOptions
		}
		parts := strings.Split(param, ".")
		for _, p := range parts[:len(parts)-1] {
			next, ok := obj[p].(map[string]any)
			if !ok {
				return "", ErrOptions
			}
			obj = next
		}
		key := parts[len(parts)-1]
		if _, exists := obj[key]; !exists {
			d.order[reflect.ValueOf(obj)] = append(d.order[reflect.ValueOf(obj)], key)
		}
		obj[key] = value
		return d.jsonBody(d.Value, true)
	}
	q, err := url.ParseQuery(body)
	if err != nil || len(q) == 0 {
		return "", ErrOptions
	}
	if _, exists := q[param]; !exists {
		return "", ErrOptions
	}
	q.Set(param, fmt.Sprint(value))
	return q.Encode(), nil
}

// ResourceMatches permits only this immutable endpoint, its configured
// pagination parameter and the existing bounded size probe. It grants no
// claim or database authority and cannot change an origin or resource path.
func (o Options) ResourceMatches(raw string) bool {
	if raw == o.Endpoint {
		return true
	}
	if o.Pagination == nil {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil {
		return false
	}
	base, _ := url.Parse(o.Endpoint)
	if u.Scheme != base.Scheme || u.Host != base.Host || u.User != nil || u.Opaque != "" || u.Fragment != "" || u.EscapedPath() != base.EscapedPath() {
		return false
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return false
	}
	original, err := url.ParseQuery(base.RawQuery)
	if err != nil {
		return false
	}
	for k, values := range q {
		if o.Pagination.Location == "query" && k == o.Pagination.Param {
			if len(values) != 1 {
				return false
			}
			s := values[0]
			if tpl := o.Pagination.ValueTemplate; tpl != "" {
				at := strings.Index(tpl, "{value}")
				if at < 0 {
					return false
				}
				prefix, suffix := tpl[:at], tpl[at+7:]
				if !strings.HasPrefix(s, prefix) || !strings.HasSuffix(s, suffix) {
					return false
				}
				s = s[len(prefix) : len(s)-len(suffix)]
			}
			n, err := strconv.Atoi(s)
			if err != nil || n < 0 || n > 1000000000 || strconv.Itoa(n) != s {
				return false
			}
			continue
		}
		if name, location, _ := sizeParam(o); location == "query" && k == name && len(values) == 1 && values[0] == "100" {
			continue
		}
		if !reflect.DeepEqual(values, original[k]) {
			return false
		}
	}
	for k, values := range original {
		if k == o.Pagination.Param && o.Pagination.Location == "query" {
			continue
		}
		if !reflect.DeepEqual(q[k], values) {
			name, location, _ := sizeParam(o)
			if k != name || location != "query" || len(q[k]) != 1 || q[k][0] != "100" {
				return false
			}
		}
	}
	return true
}
