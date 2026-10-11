package apisniffer

import (
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

// The original live helper binds the first matching response even if reading
// its JSON fails. Retain that URL for a bounded replay instead of selecting a
// later response. Body/reflection/policy checks stay in the capture controller.
func NewLiveBrowserReplayExchange(source, method string, headers http.Header, body []byte) (BrowserReplayExchange, error) {
	if len(body) > 64<<20 {
		return BrowserReplayExchange{}, ErrInventory
	}
	exchange, err := NewBrowserReplayExchange(source, method, headers, []byte("null"))
	if err != nil {
		return exchange, err
	}
	exchange.document, err = Decode(body)
	if err != nil {
		exchange.document = nil
	}
	return exchange, nil
}

// The qualified rotating-token subset uses literal hosts and '*' path globs.
// A wildcard can never grant another origin or forward a captured query.
func validLiveURLPattern(endpoint, pattern string) bool {
	u, err := url.Parse(endpoint)
	return err == nil && u.Scheme == "https" && u.Host != "" && u.User == nil && u.Fragment == "" && len(pattern) <= 2048 && strings.HasPrefix(pattern, u.Host+"/") && !strings.ContainsAny(pattern, "?[]#\\\x00\r\n")
}

func (o BrowserReplayOptions) LiveURLMatches(source, method string) bool {
	if o.LiveURLMatch == "" || method != o.Inventory.Method || !validLiveURLPattern(o.Inventory.Endpoint, o.LiveURLMatch) {
		return false
	}
	u, err := url.Parse(source)
	base, _ := url.Parse(o.Inventory.Endpoint)
	if err != nil || u.Scheme != base.Scheme || u.Host != base.Host || u.User != nil || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	pattern := strings.ReplaceAll(regexp.QuoteMeta(o.LiveURLMatch), `\*`, `.*`)
	return regexp.MustCompile(`\A(?:` + pattern + `)\z`).MatchString(u.Host + u.EscapedPath())
}

// Python's live URL helper takes the first matching response and changes only
// the stored endpoint's base. Keep configured query and pagination controls.
func SelectLiveBrowserReplayExchange(o BrowserReplayOptions, exchanges []BrowserReplayExchange) (string, *Document, bool) {
	for _, exchange := range exchanges {
		if !o.LiveURLMatches(exchange.url, exchange.method) {
			continue
		}
		base, _ := url.Parse(o.Inventory.Endpoint)
		live, _ := url.Parse(exchange.url)
		base.Path, base.RawPath = live.Path, live.RawPath
		return base.String(), exchange.document, true
	}
	return o.Inventory.Endpoint, nil, false
}

// Publisher evidence keeps the observed path but never exports a browser's
// rotating query credentials. Ordinary captures retain their configured URL.
func (o BrowserReplayOptions) PublicCaptureResource(source string) string {
	if !o.LiveURLMatches(source, o.Inventory.Method) {
		return o.Inventory.Endpoint
	}
	base, _ := url.Parse(o.Inventory.Endpoint)
	live, _ := url.Parse(source)
	base.Path, base.RawPath = live.Path, live.RawPath
	return base.String()
}

// Map an already validated logical request onto this session's observed path.
// Only the original inventory writer/config fingerprint leaves the renderer.
func (o BrowserReplayOptions) LiveRequestURL(request, endpoint string) (string, error) {
	if !o.Inventory.ResourceMatches(request) || !o.LiveURLMatches(endpoint, o.Inventory.Method) {
		return "", ErrOptions
	}
	u, err := url.Parse(request)
	if err != nil {
		return "", ErrOptions
	}
	live, _ := url.Parse(endpoint)
	u.Path, u.RawPath = live.Path, live.RawPath
	bound := o.Inventory
	bound.Endpoint = endpoint
	if !bound.ResourceMatches(u.String()) {
		return "", ErrOptions
	}
	return u.String(), nil
}
