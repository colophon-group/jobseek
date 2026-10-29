package apisniffer

import (
	"net/http"
	"net/url"
	"strings"
	"unicode/utf8"
)

// Only the public source and JSON paths are configuration. Refreshed values
// belong to one discovery and never become profile or persisted configuration.
type requestHeaderRefresh struct {
	source string
	fields map[string]string
}

func (*requestHeaderRefresh) String() string   { return "public request header refresh" }
func (*requestHeaderRefresh) GoString() string { return "public request header refresh" }

func parseRequestHeaderRefresh(raw any, o Options) (*requestHeaderRefresh, error) {
	if raw == nil {
		return nil, nil
	}
	m, ok := raw.(map[string]any)
	if !ok || len(m) != 2 {
		return nil, ErrOptions
	}
	source, ok := m["source_url"].(string)
	fields, valid := m["fields"].(map[string]any)
	if !ok || !valid || !validURL(source) || source == o.BoardURL || source == o.Endpoint || len(fields) < 1 || len(fields) > 16 || o.PostDataRefresh != nil {
		return nil, ErrOptions
	}
	base, err := url.Parse(o.BoardURL)
	u, parseErr := url.Parse(source)
	if err != nil || parseErr != nil || !validURL(o.BoardURL) || u.Scheme != base.Scheme || u.Host != base.Host {
		return nil, ErrOptions
	}
	refresh := &requestHeaderRefresh{source: source, fields: map[string]string{}}
	for name, value := range fields {
		path, ok := value.(string)
		if !ok || !validHeaderName(name) || len(name) > 256 || len(path) < 1 || len(path) > 512 || !utf8.ValidString(path) || strings.ContainsRune(path, 0) || o.Headers.Get(name) != "" {
			return nil, ErrOptions
		}
		switch strings.ToLower(name) {
		case "host", "cookie", "set-cookie", "connection", "content-length", "transfer-encoding", "accept-encoding", "origin", "referer":
			return nil, ErrOptions
		}
		canonical := http.CanonicalHeaderKey(name)
		if _, exists := refresh.fields[canonical]; exists {
			return nil, ErrOptions
		}
		refresh.fields[canonical] = path
	}
	return refresh, nil
}

func (o Options) IsRequestHeaderRefreshRequest(r Request) bool {
	return o.RequestHeaderRefresh != nil && r.Method == http.MethodGet && r.Body == "" && len(r.Headers) == 0 && !r.Probe && (r.URL == o.BoardURL || r.URL == o.RequestHeaderRefresh.source)
}

func (o Options) refreshedRequestHeaders(d *Document) (Options, error) {
	if o.RequestHeaderRefresh == nil || d == nil {
		return o, ErrInventory
	}
	o.Headers = o.Headers.Clone()
	for name, path := range o.RequestHeaderRefresh.fields {
		value, err := Search(d.Value, path)
		text, ok := value.(string)
		if err != nil || !ok || len(text) < 1 || len(text) > 8192 || !utf8.ValidString(text) || strings.IndexFunc(text, func(r rune) bool { return r < 32 || r == 127 }) >= 0 {
			return o, ErrInventory
		}
		o.Headers.Set(name, text)
	}
	return o, nil
}
