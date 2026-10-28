package apisniffer

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/url"
	"reflect"
	"slices"
	"strings"
)

// DiscoverBrowserReplay keeps the established parser and pagination traversal.
// Read the initial document once, set Python's browser/HTTP page budget from
// that document, then seed Discover so capture reuse does not issue a replay.
func DiscoverBrowserReplay(ctx context.Context, o BrowserReplayOptions, fetch Fetch, join JoinURL, httpFallback bool, domLinks ...DOMLinks) (Inventory, error) {
	if fetch == nil || join == nil || len(domLinks) > 1 {
		return Inventory{}, ErrOptions
	}
	firstRequest := Request{Method: o.Inventory.Method, URL: o.Inventory.Endpoint, Body: o.Inventory.Body, Headers: o.Inventory.Headers.Clone()}
	first, err := fetch(ctx, firstRequest)
	if err != nil {
		return Inventory{}, err
	}
	limit, err := o.PaginationLimit(first, httpFallback)
	if err != nil {
		return Inventory{}, err
	}
	inventory := o.Inventory
	if len(domLinks) == 1 && domLinks[0] != nil && inventory.URLField == "" && inventory.URLTemplate == "" && !inventory.HTML {
		inventory.domLinks = domLinks[0]
	}
	if inventory.Pagination != nil {
		pagination := *inventory.Pagination
		pagination.MaxPages = limit
		inventory.Pagination = &pagination
	}
	initial := true
	result, failure := Discover(ctx, inventory, func(call context.Context, request Request) (*Document, error) {
		if initial {
			initial = false
			return first, nil
		}
		return fetch(call, request)
	}, join)
	// Original browser replay returns URLs when fields are not declared.
	result.URLOnly = len(o.Inventory.Fields) == 0
	return result, failure
}

// BrowserReplayOptions retains the existing inventory parser and page requests.
// Admission must also require the affine capture/fetch controller; these options
// alone never authorize a browser profile or replace its legacy owner.
type BrowserReplayOptions struct {
	BootstrapURL        string // Code-owned navigation alias; never a metadata override.
	WarmupURL           string // Same-origin navigation in the same disposable target.
	LiveURLMatch        string
	ResponseBodyLimit   int // Fixed native provider factory only; never metadata-controlled.
	Inventory           Options
	Wait                string
	TimeoutMS, SettleMS uint64
	pageCapConfigured   bool
}

// BootstrapAliasMatches verifies the one code-owned network alias against the
// canonical board and explicit API. A detached option cannot grant another URL.
func (o BrowserReplayOptions) BootstrapAliasMatches(board, navigation string) bool {
	return o.Inventory.BoardURL == board && o.BootstrapURL == navigation &&
		board == "https://careers.trip.com/#/jobList" && navigation == "https://careers.trip.com/" &&
		o.Inventory.Endpoint == "https://careers.trip.com/api/oversea/getOverseaJobAd" && o.Inventory.Method == "POST"
}

func BrowserReplayOptionsFromMetadata(boardURL, raw string) (BrowserReplayOptions, error) {
	o := BrowserReplayOptions{BootstrapURL: boardURL, Wait: "load", TimeoutMS: 20000, SettleMS: 3000}
	d, e := Decode([]byte(raw))
	if e != nil {
		return o, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok || m["browser"] != true {
		return o, ErrOptions
	}
	if value, present := m["warmup_url"]; present {
		warmup, ok := value.(string)
		w, err := url.Parse(warmup)
		board, other := url.Parse(boardURL)
		if !ok || !validURL(warmup) || err != nil || other != nil || w.Scheme != "https" || w.Host != board.Host || w.Fragment != "" {
			return o, ErrOptions
		}
		o.WarmupURL = warmup
		delete(m, "warmup_url")
		d.order[reflect.ValueOf(m)] = slices.DeleteFunc(d.order[reflect.ValueOf(m)], func(key string) bool { return key == "warmup_url" })
	}
	// Trip.com's hash is a client-side listing route, not a network resource.
	// Its same-origin root establishes the affine session for the explicit API.
	// Retain the canonical board URL for identity and URL assembly, and grant
	// this navigation alias only to the independently verified API endpoint.
	if boardURL == "https://careers.trip.com/#/jobList" && m["api_url"] == "https://careers.trip.com/api/oversea/getOverseaJobAd" && m["method"] == "POST" {
		o.BootstrapURL = "https://careers.trip.com/"
	}
	if raw, present := m["api_url_match"]; present {
		pattern, ok := raw.(string)
		endpoint, _ := m["api_url"].(string)
		if !ok || !validLiveURLPattern(endpoint, pattern) || m["method"] != nil && m["method"] != "GET" {
			return o, ErrOptions
		}
		o.LiveURLMatch = pattern
		delete(m, "api_url_match")
		d.order[reflect.ValueOf(m)] = slices.DeleteFunc(d.order[reflect.ValueOf(m)], func(key string) bool { return key == "api_url_match" })
	}
	if _, present := m["post_data_refresh"]; present {
		return o, ErrOptions
	}
	if _, present := m["response_decrypt"]; present {
		return o, ErrOptions // Captured encrypted response ranking is not qualified.
	}
	// Explicit actions, interception, stealth and persistent
	// identities remain unsupported until the corresponding controller exists.
	// OptionsFromMetadata rejects unknown keys instead of discarding controls.
	m["browser"] = false
	if value, present := m["wait"]; present {
		wait, ok := value.(string)
		if !ok || wait != "commit" && wait != "domcontentloaded" && wait != "load" && wait != "networkidle" {
			return o, ErrOptions
		}
		o.Wait = wait
	}
	if value, present := m["timeout"]; present {
		n, ok := integer(value)
		if !ok || n < 1 || n > 120000 {
			return o, ErrOptions
		}
		o.TimeoutMS = uint64(n)
	}
	if value, present := m["settle"]; present {
		n, ok := value.(json.Number)
		if !ok {
			return o, ErrOptions
		}
		seconds, e := n.Float64()
		if e != nil || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds < 0 || seconds > 30 || seconds*1000 != math.Trunc(seconds*1000) {
			return o, ErrOptions
		}
		o.SettleMS = uint64(seconds * 1000)
	}
	// Preserve configured object order in POST bodies and field expressions.
	metadata, e := d.jsonBody(m, false)
	if e != nil {
		return o, ErrOptions
	}
	if pg, ok := m["pagination"].(map[string]any); ok {
		_, o.pageCapConfigured = pg["max_pages"]
	}
	o.Inventory, e = OptionsFromMetadata(o.BootstrapURL, metadata)
	if e != nil {
		return o, e
	}
	o.Inventory.BoardURL = boardURL
	if o.Inventory.PDFProof != nil {
		return o, ErrOptions
	}
	// HTTP discovery infers fields; original browser replay only uses an
	// explicit field map. Preserve URL-only inventory and detail scheduling.
	o.Inventory.AutoFields = false
	if o.Inventory.HTML {
		// HTML browser interception has a different original traversal contract.
		return o, ErrOptions
	}
	endpoint, _ := url.Parse(o.Inventory.Endpoint)
	// Aura requires a separate bounded interaction pass when navigation only
	// captures non-listing actions. Keep that contract out of initial admission.
	if strings.HasSuffix(endpoint.Path, "/s/sfsites/aura") {
		return o, ErrOptions
	}
	return o, nil
}

// BrowserReplayExchange exists only within one controller's browser context.
// It deliberately cannot serialize or format private capture material.
type BrowserReplayExchange struct {
	url, method string
	headers     http.Header
	document    *Document
}

func (BrowserReplayExchange) String() string               { return "private browser replay exchange" }
func (BrowserReplayExchange) GoString() string             { return "private browser replay exchange" }
func (BrowserReplayExchange) MarshalJSON() ([]byte, error) { return nil, ErrOptions }

func NewBrowserReplayExchange(source, method string, headers http.Header, body []byte) (BrowserReplayExchange, error) {
	e := BrowserReplayExchange{}
	method = strings.ToUpper(method)
	if !validURL(source) || method != "GET" && method != "POST" || len(body) > 64<<20 {
		return e, ErrInventory
	}
	d, err := Decode(body)
	if err != nil {
		return e, err
	}
	copyHeaders := http.Header{}
	for key, values := range headers {
		if !validHeaderName(key) {
			return e, ErrInventory
		}
		for _, value := range values {
			if len(value) > 8192 || strings.ContainsAny(value, "\r\n\x00") {
				return e, ErrInventory
			}
		}
		copyHeaders[key] = append([]string{}, values...)
	}
	return BrowserReplayExchange{source, method, copyHeaders, d}, nil
}

// SelectBrowserReplayExchange reproduces Python's matching host/path/method,
// object-count ranking, first-wins tie and zero-score header refresh behavior.
// Query values can rotate during navigation; they are never copied into the
// configured replay URL by this selector. Policy must be checked at capture.
func SelectBrowserReplayExchange(o BrowserReplayOptions, exchanges []BrowserReplayExchange) (http.Header, *Document, bool, error) {
	api, e := url.Parse(o.Inventory.Endpoint)
	if e != nil || api.Host == "" {
		return nil, nil, false, ErrOptions
	}
	best, score := -1, -1
	for index, exchange := range exchanges {
		u, e := url.Parse(exchange.url)
		if e != nil || exchange.method != o.Inventory.Method || u.Host != api.Host || u.EscapedPath() != api.EscapedPath() || exchange.document == nil {
			continue
		}
		value, e := Search(exchange.document.Value, o.Inventory.Path)
		if e != nil {
			value = nil
		}
		current := 0
		switch value := value.(type) {
		case []any:
			for _, item := range value {
				if _, ok := item.(map[string]any); ok {
					current++
				}
			}
		case map[string]any:
			current = 1
		}
		if current > score {
			best, score = index, current
		}
	}
	if best < 0 {
		return nil, nil, false, nil
	}
	d := exchanges[best].document
	if score == 0 {
		d = nil
	}
	return exchanges[best].headers.Clone(), d, true, nil
}

// PaginationLimit preserves Python's browser default (50), HTTP fallback
// default (200), explicit cap, and initial known-total expansion up to 200.
// Controllers compute it before the existing size probe/pagination traversal.
func (o BrowserReplayOptions) PaginationLimit(first *Document, httpFallback bool) (int, error) {
	if o.Inventory.Pagination == nil {
		return 1, nil
	}
	limit := o.Inventory.Pagination.MaxPages
	if !o.pageCapConfigured {
		limit = 50
		if httpFallback {
			limit = 200
		}
	}
	if first == nil {
		return limit, nil
	}
	items, e := first.items(o.Inventory.Path, o.Inventory.PathValues)
	if e != nil {
		return 0, e
	}
	total, known := first.total(o.Inventory.Path, o.Inventory.TotalPath)
	if known && total > 0 && len(items) > 0 && limit < 200 {
		needed := (total + len(items) - 1) / len(items)
		if needed > limit {
			limit = min(needed, 200)
		}
	}
	return limit, nil
}

// BrowserReplayDocument exposes decoded public response data to the local
// detail selector; captured request credentials remain inaccessible.
func BrowserReplayDocument(exchange BrowserReplayExchange) *Document { return exchange.document }
