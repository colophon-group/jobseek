package apisniffer

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

type NextdataPagination struct {
	Path, PageCount, TotalRecords, Param, Template string
	Size, Start, Concurrency                       int
	Offset                                         bool
}

type NextdataOptions struct {
	BoardURL, Source, Path, Template, ExpectedTitle, URLAllowlist string
	Fields                                                        map[string]any
	SlugFields                                                    []string
	Strict                                                        bool
	Headers                                                       map[string]string
	Pagination                                                    *NextdataPagination
	IncludeItems, RequireItems                                    map[string][]string
	Metadata                                                      map[string]any
	Identity                                                      *NextdataIdentity
	BrowserDocumentTransform                                      string
	ExpectedOrganization                                          string
}

type NextdataIdentity struct{ Provider, Tenant, Field string }

var explicitNextdataIdentity = regexp.MustCompile(`^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}$`)

func (c NextdataIdentity) Valid(value string) bool {
	return strings.HasPrefix(value, c.Provider+":"+c.Tenant+":") && explicitNextdataIdentity.MatchString(value)
}

// Configuration remains detached from execution/ownership. Unknown transport
// authority and unfinished browser/provider-identity lanes cannot be adopted.
func NextdataOptionsFromMetadata(boardURL, metadata string) (NextdataOptions, error) {
	o := NextdataOptions{BoardURL: boardURL, Source: "nextdata", Fields: map[string]any{}, Headers: map[string]string{}}
	d, err := Decode([]byte(metadata))
	if err != nil {
		return o, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	o.Metadata = m
	if !validURL(boardURL) {
		return o, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "actions", "skip_ssl", "browser_expression", "base_salary", "board_gone_statuses"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return o, ErrOptions
	}
	for key, target := range map[string]*string{"source": &o.Source, "path": &o.Path, "url_template": &o.Template, "expected_page_title": &o.ExpectedTitle, "url_allowlist": &o.URLAllowlist} {
		if value := m[key]; value != nil {
			var ok bool
			*target, ok = value.(string)
			if !ok || len(*target) > 8192 || strings.ContainsRune(*target, 0) {
				return o, ErrOptions
			}
		}
	}
	if o.Source == "browser" || o.Path == "" || o.Template == "" {
		return o, ErrOptions
	}
	if _, err := Search(map[string]any{}, o.Path); err != nil {
		return o, ErrOptions
	}
	if m["strict_path"] != nil {
		var ok bool
		o.Strict, ok = m["strict_path"].(bool)
		if !ok {
			return o, ErrOptions
		}
	}
	if value := m["fields"]; value != nil {
		var ok bool
		o.Fields, ok = value.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
	}
	for _, spec := range o.Fields {
		if ValidateField(spec) != nil {
			return o, ErrOptions
		}
	}
	if value := m["slug_fields"]; value != nil {
		a, ok := value.([]any)
		if !ok {
			return o, ErrOptions
		}
		for _, v := range a {
			s, ok := v.(string)
			if !ok || s == "" || len(s) > 256 {
				return o, ErrOptions
			}
			o.SlugFields = append(o.SlugFields, s)
		}
	}
	if o.URLAllowlist != "" {
		if len(o.URLAllowlist) > 2048 {
			return o, ErrOptions
		}
		if _, err := dom.CompileURLPattern(o.URLAllowlist); err != nil {
			return o, ErrOptions
		}
	}
	if raw := m["expected_hiring_organization"]; raw != nil {
		value, ok := raw.(string)
		if !ok || strings.TrimSpace(value) == "" || len(value) > 256 || strings.ContainsRune(value, 0) || o.URLAllowlist == "" {
			return o, ErrOptions
		}
		o.ExpectedOrganization = strings.TrimSpace(value)
	}
	if raw := m["source_identity"]; raw != nil {
		cfg, ok := raw.(map[string]any)
		if !ok || len(cfg) != 3 || len(o.Fields) == 0 || o.URLAllowlist == "" {
			return o, ErrOptions
		}
		id := &NextdataIdentity{}
		for key, target := range map[string]*string{"provider": &id.Provider, "tenant": &id.Tenant, "field": &id.Field} {
			*target, ok = cfg[key].(string)
			if !ok || *target == "" || len(*target) > 256 || strings.ContainsRune(*target, 0) {
				return o, ErrOptions
			}
		}
		if !id.Valid(id.Provider + ":" + id.Tenant + ":1") {
			return o, ErrOptions
		}
		if _, err := Search(map[string]any{}, id.Field); err != nil {
			return o, ErrOptions
		}
		o.Identity = id
	}
	if o.ExpectedTitle != "" {
		o.ExpectedTitle = strings.TrimSpace(o.ExpectedTitle)
		if len(o.ExpectedTitle) > 256 {
			return o, ErrOptions
		}
	}
	if raw := m["request_headers"]; raw != nil {
		fields, ok := raw.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		for k, v := range fields {
			s, ok := v.(string)
			if !ok {
				return o, ErrOptions
			}
			o.Headers[k] = s
		}
		if jsonld.ValidateDocumentOptions(jsonld.DocumentOptions{Headers: o.Headers, PublicHeaders: true}) != nil {
			return o, ErrOptions
		}
	}
	for key, target := range map[string]*map[string][]string{"include_item_values": &o.IncludeItems, "require_item_values": &o.RequireItems} {
		if raw := m[key]; raw != nil {
			fields, ok := raw.(map[string]any)
			if !ok || len(fields) == 0 || len(fields) > 16 {
				return o, ErrOptions
			}
			*target = map[string][]string{}
			for k, v := range fields {
				if k == "" || len(k) > 256 {
					return o, ErrOptions
				}
				if _, err := Search(map[string]any{}, k); err != nil {
					return o, ErrOptions
				}
				values, ok := v.([]any)
				if !ok || len(values) == 0 {
					return o, ErrOptions
				}
				for _, v := range values {
					s, ok := v.(string)
					if !ok || s == "" || len(s) > 256 || strings.ContainsRune(s, 0) {
						return o, ErrOptions
					}
					(*target)[k] = append((*target)[k], s)
				}
			}
		}
	}
	if raw := m["pagination"]; raw != nil {
		cfg, ok := raw.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		p := &NextdataPagination{Start: 1, Concurrency: 5, Param: "page"}
		for k := range cfg {
			switch k {
			case "path", "page_count", "total_records", "page_size", "start", "concurrency", "mode", "offset_param", "page_param", "url_template":
			default:
				return o, ErrOptions
			}
		}
		for key, target := range map[string]*string{"path": &p.Path, "page_count": &p.PageCount, "total_records": &p.TotalRecords, "url_template": &p.Template} {
			if raw := cfg[key]; raw != nil {
				var ok bool
				*target, ok = raw.(string)
				if !ok {
					return o, ErrOptions
				}
			}
		}
		if mode := cfg["mode"]; mode != nil {
			if mode != "page" && mode != "offset" {
				return o, ErrOptions
			}
			p.Offset = mode == "offset"
		}
		key := "page_param"
		if p.Offset {
			p.Param = "from"
			key = "offset_param"
		}
		if raw := cfg[key]; raw != nil {
			var ok bool
			p.Param, ok = raw.(string)
			if !ok || p.Param == "" {
				return o, ErrOptions
			}
		}
		for key, target := range map[string]*int{"page_size": &p.Size, "start": &p.Start, "concurrency": &p.Concurrency} {
			if raw := cfg[key]; raw != nil {
				var ok bool
				*target, ok = integer(raw)
				if !ok {
					return o, ErrOptions
				}
			}
		}
		if p.Start < 0 || p.Size < 0 || p.Concurrency < 1 || p.Concurrency > 5 || p.Path == "" || p.PageCount == "" && (p.TotalRecords == "" || p.Size == 0) {
			return o, ErrOptions
		}
		if p.Template != "" && (strings.Count(p.Template, "{page}") != 1 || p.Offset) {
			return o, ErrOptions
		}
		for _, path := range []string{p.Path, p.PageCount, p.TotalRecords} {
			if path != "" {
				if _, err := Search(map[string]any{}, path); err != nil {
					return o, ErrOptions
				}
			}
		}
		if o.ExpectedTitle != "" || len(o.IncludeItems) > 0 && p.TotalRecords != "" {
			return o, ErrOptions
		}
		o.Pagination = p
	}
	return o, nil
}

func (o NextdataOptions) PageURL(index int) (string, error) {
	if o.Pagination == nil || index < 1 {
		return "", ErrOptions
	}
	p := o.Pagination
	if p.Template != "" {
		value := strings.ReplaceAll(p.Template, "{page}", nextdataIntString(p.Start+index))
		u, err := url.Parse(value)
		base, _ := url.Parse(o.BoardURL)
		if err != nil || !validURL(value) || u.Scheme != base.Scheme || u.Host != base.Host {
			return "", ErrOptions
		}
		return value, nil
	}
	u, err := url.Parse(o.BoardURL)
	if err != nil {
		return "", err
	}
	v := p.Start + index
	if p.Offset {
		v = p.Size * index
	}
	u.RawQuery = nextdataQuery(u.RawQuery, p.Param, nextdataIntString(v))
	return u.String(), nil
}

func nextdataIntString(value int) string { b, _ := json.Marshal(value); return string(b) }

func (o NextdataOptions) ResourceMatches(value string) bool {
	if value == o.BoardURL {
		return true
	}
	if o.Pagination == nil {
		return false
	}
	p := o.Pagination
	var raw string
	if p.Template != "" {
		pieces := strings.Split(p.Template, "{page}")
		if !strings.HasPrefix(value, pieces[0]) || !strings.HasSuffix(value, pieces[1]) {
			return false
		}
		raw = strings.TrimSuffix(strings.TrimPrefix(value, pieces[0]), pieces[1])
	} else {
		u, err := url.Parse(value)
		if err != nil {
			return false
		}
		values := u.Query()[p.Param]
		if len(values) != 1 {
			return false
		}
		raw = values[0]
	}
	n, err := strconv.Atoi(raw)
	if err != nil || strconv.Itoa(n) != raw {
		return false
	}
	index := n - p.Start
	if p.Offset {
		if p.Size <= 0 || n%p.Size != 0 {
			return false
		}
		index = n / p.Size
	}
	if index < 1 || index > 50_000 {
		return false
	}
	expected, err := o.PageURL(index)
	return err == nil && expected == value
}

func (o NextdataOptions) DetailWitnessMatches(value string) bool {
	if o.ExpectedOrganization == "" || !validURL(value) || len(value) > 8192 {
		return false
	}
	pattern, err := dom.CompileURLPattern(o.URLAllowlist)
	if err != nil {
		return false
	}
	m, err := pattern.FindStringMatch(value)
	if err != nil || m == nil {
		return false
	}
	at, n := m.ByteRange()
	return at == 0 && n == len(value)
}

// Python parse_qs preserves first key order and blank values; urlencode
// then emits all values for that key together. Retain the page URL bytes.
func nextdataQuery(raw, param, value string) string {
	keys := []string{}
	values := map[string][]string{}
	for _, part := range strings.Split(raw, "&") {
		if part == "" {
			continue
		}
		pair := strings.SplitN(part, "=", 2)
		if len(pair) == 1 {
			pair = append(pair, "")
		}
		key, err := url.QueryUnescape(pair[0])
		if err != nil {
			continue
		}
		v, err := url.QueryUnescape(pair[1])
		if err != nil {
			continue
		}
		if _, ok := values[key]; !ok {
			keys = append(keys, key)
		}
		values[key] = append(values[key], v)
	}
	if _, ok := values[param]; !ok {
		keys = append(keys, param)
	}
	values[param] = []string{value}
	parts := []string{}
	for _, key := range keys {
		for _, v := range values[key] {
			parts = append(parts, url.QueryEscape(key)+"="+url.QueryEscape(v))
		}
	}
	return strings.Join(parts, "&")
}
