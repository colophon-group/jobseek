package apisniffer

import (
	"net/url"
	"strings"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"github.com/jmespath/go-jmespath"
)

type InlineFetchCandidate struct {
	URL      string
	Document jsonld.DocumentOptions
}
type InlineMonitorOptions struct {
	InlineOptions
	BoardURL, Contains, JSONPath string
	Candidates                   []InlineFetchCandidate
	Attempts                     int
	Transient403                 bool
}

func InlineMonitorOptionsFromMetadata(boardURL, raw string) (InlineMonitorOptions, error) {
	o := InlineMonitorOptions{BoardURL: boardURL, Attempts: 3}
	m, err := DecodeInlineMetadata(raw)
	if err != nil {
		return o, err
	}
	if !jsonld.ValidPublicEndpoint(boardURL) {
		return o, ErrOptions
	}
	for _, key := range []string{"render", "proxy", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	if v := m["ssl_verify"]; v != nil && v != true {
		return o, ErrOptions
	}
	o.InlineOptions, err = InlineDocumentOptions(m)
	if err != nil {
		return o, err
	}
	o.Transient403, err = inlineBool(m, "transient_403")
	if err != nil {
		return o, err
	}
	if v := m["transport_attempts"]; v != nil {
		n, ok := integer(v)
		if !ok || n < 1 || n > 5 {
			return o, ErrOptions
		}
		o.Attempts = n
	}
	if v := m["fetch_contains"]; v != nil {
		s, ok := v.(string)
		if !ok || len(s) > 1<<20 {
			return o, ErrOptions
		}
		o.Contains = s
	}
	o.JSONPath, err = inlineOptionalText(m, "fetch_json_path", 256)
	if err != nil {
		return o, err
	}
	if o.JSONPath != "" {
		if _, err = jmespath.Compile(o.JSONPath); err != nil {
			return o, ErrOptions
		}
	}
	add := func(raw any) error {
		c := InlineFetchCandidate{}
		switch v := raw.(type) {
		case string:
			c.URL = v
		case map[string]any:
			c.URL, _ = v["url"].(string)
			if v["headers"] != nil {
				h, ok := v["headers"].(map[string]any)
				if !ok {
					return ErrOptions
				}
				c.Document.Headers = map[string]string{}
				for k, v := range h {
					s, ok := v.(string)
					if !ok {
						return ErrOptions
					}
					c.Document.Headers[k] = s
					if strings.EqualFold(strings.TrimSpace(k), "X-No-Cache") {
						c.Document.InlineCacheBypass = true
					}
				}
				c.Document.PublicHeaders = len(h) > 0
				c.Document.SameOrigin = len(h) > 0
			}
		default:
			return ErrOptions
		}
		if !jsonld.ValidPublicEndpoint(c.URL) || jsonld.ValidateDocumentOptions(c.Document) != nil {
			return ErrOptions
		}
		o.Candidates = append(o.Candidates, c)
		return nil
	}
	if m["fetch_urls"] != nil {
		a, ok := m["fetch_urls"].([]any)
		if !ok || len(a) == 0 || len(a) > 32 {
			return o, ErrOptions
		}
		for _, v := range a {
			if err = add(v); err != nil {
				return o, err
			}
		}
	} else {
		endpoint := boardURL
		if detailTruthy(m["fetch_url"]) {
			s, ok := m["fetch_url"].(string)
			if !ok {
				return o, ErrOptions
			}
			endpoint = s
		}
		if err = add(endpoint); err != nil {
			return o, err
		}
	}
	return o, nil
}
func (o InlineMonitorOptions) ResourceMatches(resource string) bool {
	for _, c := range o.Candidates {
		if resource == c.URL {
			return true
		}
	}
	return false
}

// Detail source URLs retain the canonical board origin; synthetic IDs retain
// the canonical URL with only the reserved identity query replaced.
func (o InlineMonitorOptions) PostingURLMatches(raw string) bool {
	board, err := url.Parse(o.BoardURL)
	if err != nil {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || !strings.EqualFold(u.Scheme, board.Scheme) || !strings.EqualFold(u.Hostname(), board.Hostname()) || u.Port() != board.Port() {
		return false
	}
	if o.SourceURLSelector != "" {
		return true
	}
	u.RawQuery = board.RawQuery
	u.ForceQuery = board.ForceQuery
	return u.String() == board.String()
}
