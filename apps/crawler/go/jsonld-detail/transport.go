package jsonld

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
	"golang.org/x/net/html/charset"
	"golang.org/x/text/encoding/charmap"
)

var allowedConfig = map[string]bool{}

func init() {
	for _, k := range []string{"render", "proxy", "skip_ssl", "ignore_locations", "ignore_address_region", "ignore_date_posted", "ignore_valid_through", "defaults_by_url", "defaults", "enrich", "fallback", "transport_attempts", "description_selector", "request_headers", "wait", "actions", "channel", "stealth", "timeout", "headless", "persistent_context", "wait_fallback", "browser_backend", "routing_revision"} {
		allowedConfig[k] = true
	}
}
func ValidateConfig(config map[string]any) error {
	for k := range config {
		if !allowedConfig[k] {
			return fmt.Errorf("unsupported direct JSON-LD config: %s", k)
		}
	}
	for _, k := range []string{"render", "proxy", "skip_ssl"} {
		if pyTruthy(config[k]) {
			return errors.New("JSON-LD requires direct verified transport")
		}
	}
	if v := config["transport_attempts"]; v != nil {
		n, ok := v.(float64)
		if !ok || n < 1 || n > 5 || float64(int(n)) != n {
			return errors.New("JSON-LD transport_attempts must be 1..5")
		}
	}
	if h := config["request_headers"]; pyTruthy(h) {
		m, ok := h.(map[string]any)
		if !ok {
			return errors.New("invalid JSON-LD request_headers")
		}
		for k, v := range m {
			if _, ok := v.(string); !ok || k == "" || strings.ContainsAny(k, "\r\n\x00") {
				return errors.New("invalid JSON-LD request header")
			}
		}
	}
	if s := config["description_selector"]; s != nil {
		selector, ok := s.(string)
		if !ok || trim(selector) == "" || len([]rune(selector)) > 256 || strings.ContainsRune(selector, 0) {
			return errors.New("invalid JSON-LD description_selector")
		}
		if _, err := cascadia.Compile(selector); err != nil {
			return errors.New("invalid JSON-LD CSS selector")
		}
	}
	return nil
}
func decodedBody(body []byte, contentType string) []byte {
	_, params, err := mime.ParseMediaType(contentType)
	if err != nil {
		return body
	}
	label := strings.ToLower(params["charset"])
	if label == "" || label == "utf-8" || label == "utf8" {
		return body
	}
	enc, _ := charset.Lookup(label)
	if label == "iso-8859-1" || label == "latin-1" || label == "latin1" {
		enc = charmap.ISO8859_1
	}
	if enc == nil {
		return body
	}
	out, err := io.ReadAll(enc.NewDecoder().Reader(bytes.NewReader(body)))
	if err != nil {
		return body
	}
	return out
}
func requestHeaders(config map[string]any) map[string]string {
	out := map[string]string{}
	raw, _ := config["request_headers"].(map[string]any)
	for k, v := range raw {
		switch strings.ToLower(k) {
		case "host", "connection", "content-length", "accept-encoding", "transfer-encoding":
			continue
		}
		out[k] = v.(string)
	}
	return out
}

var avatureDetailRE = regexp.MustCompile(`(?i)/[^/]*careers[^/]*/(?:job|folder|pipeline)detail(?:/|$)|/(?:folder|pipeline)detail(?:/|$)`)
var avatureHostDetailRE = regexp.MustCompile(`(?i)/(?:job|folder|pipeline)detail(?:/|$)`)

func avatureDetail(rawURL string) bool {
	u, err := url.Parse(rawURL)
	return err == nil && (avatureDetailRE.MatchString(u.Path) || (strings.HasSuffix(strings.ToLower(u.Hostname()), ".avature.net") && avatureHostDetailRE.MatchString(u.Path)))
}

type sleepFunc func(context.Context, time.Duration) error

func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func fetchHTML(ctx context.Context, client requestDoer, rawURL string, stats *FetchResult, headers map[string]string, wait sleepFunc) ([]byte, error) {
	used := map[int]int{}
	total := 0
	for {
		body, err := fetchPage(ctx, client, rawURL, stats, headers)
		if err != nil {
			return nil, err
		}
		limit := 0
		if stats.Status == 403 {
			limit = 1
		}
		if stats.Status == 406 && avatureDetail(rawURL) {
			limit = 2
		}
		if used[stats.Status] >= limit {
			if stats.Status < 200 || stats.Status >= 300 {
				stats.ErrorKind = "status"
				return nil, fmt.Errorf("JSON-LD HTTP status %d", stats.Status)
			}
			return body, nil
		}
		used[stats.Status]++
		total++
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(total-1)) * (0.5 + rand.Float64()))
		if err := wait(ctx, delay); err != nil {
			return nil, err
		}
	}
}
func iframeURL(rawURL string, body []byte) (string, error) {
	requested, err := url.Parse(rawURL)
	if err != nil || requested.Scheme != "https" || !strings.HasSuffix(strings.ToLower(requested.Hostname()), ".icims.com") || requested.User != nil || requested.Port() != "" && requested.Port() != "443" {
		return "", nil
	}
	z := html.NewTokenizer(bytes.NewReader(body))
	trusted := map[string]bool{}
	for {
		kind := z.Next()
		if kind == html.ErrorToken {
			break
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		token := z.Token()
		if token.Data != "iframe" {
			continue
		}
		src := ""
		for _, a := range token.Attr {
			if a.Key == "src" {
				src = a.Val
			}
		}
		if src == "" {
			continue
		}
		u, err := requested.Parse(src)
		if err != nil {
			continue
		}
		q, err := url.ParseQuery(u.RawQuery)
		if err == nil && u.Scheme == requested.Scheme && u.Hostname() == requested.Hostname() && u.Port() == requested.Port() && u.Path == requested.Path && len(q) == 1 && len(q["in_iframe"]) == 1 && q.Get("in_iframe") == "1" && u.Fragment == "" && u.User == nil {
			trusted[u.String()] = true
		}
	}
	if len(trusted) > 1 {
		return "", errors.New("multiple trusted JSON-LD iCIMS iframes")
	}
	for u := range trusted {
		return u, nil
	}
	return "", nil
}
func selectedDescription(body []byte, selector string) (string, error) {
	tree, err := html.Parse(bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	sel, err := cascadia.Compile(selector)
	if err != nil {
		return "", err
	}
	node := cascadia.Query(tree, sel)
	if node == nil {
		return "", errors.New("JSON-LD description_selector did not match")
	}
	var out bytes.Buffer
	for child := node.FirstChild; child != nil; child = child.NextSibling {
		if err := renderHTML(&out, child); err != nil {
			return "", err
		}
	}
	selected := trim(out.String())
	if strip(selected) == "" {
		return "", errors.New("JSON-LD description_selector was empty")
	}
	return selected, nil
}
func fetchDetail(ctx context.Context, client requestDoer, request Request, wait sleepFunc) (FetchResult, error) {
	result := FetchResult{Content: emptyContent()}
	if !validEndpoint(request.URL) {
		return result, errors.New("unsupported JSON-LD URL")
	}
	if err := ValidateConfig(request.Config); err != nil {
		result.ErrorKind = "parse"
		return result, err
	}
	headers := requestHeaders(request.Config)
	var body []byte
	for attempt := 0; attempt < 2; attempt++ {
		var err error
		body, err = fetchHTML(ctx, client, request.URL, &result, headers, wait)
		if err != nil {
			return result, err
		}
		result.Content, err = Parse(request.URL, body, request.Config)
		if err != nil {
			result.ErrorKind = "parse"
			return result, err
		}
		captureText(request.URL, body)
		if pyTruthy(result.Content["title"]) {
			break
		}
		iframe, err := iframeURL(request.URL, body)
		if err != nil {
			return result, err
		}
		if iframe != "" {
			iframeBody, err := fetchHTML(ctx, client, iframe, &result, headers, wait)
			if err != nil {
				return result, err
			}
			content, err := Parse(iframe, iframeBody, request.Config)
			if err != nil {
				result.ErrorKind = "parse"
				return result, err
			}
			captureText(iframe, iframeBody)
			if pyTruthy(content["title"]) {
				body = iframeBody
				result.Content = content
				break
			}
		}
		if attempt == 0 {
			if err := wait(ctx, time.Second); err != nil {
				return result, err
			}
		}
	}
	if selector, ok := request.Config["description_selector"].(string); ok {
		selected, err := selectedDescription(body, selector)
		if err != nil {
			result.ErrorKind = "parse"
			return result, err
		}
		result.Content["description"] = selected
	}
	return result, nil
}
func FetchDetail(ctx context.Context, request Request) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return fetchDetail(ctx, client, request, sleep)
}

// FetchDetailWithClient retains this parser's HTTP, redirect, policy and retry
// contract while using an enclosing native runtime's sealed verified client.
// The caller owns trust roots, public-address checks and per-attempt cookies.
func FetchDetailWithClient(ctx context.Context, request Request, client interface {
	Do(*http.Request) (*http.Response, error)
}) (FetchResult, error) {
	if client == nil {
		return FetchResult{}, errors.New("JSON-LD HTTP client unavailable")
	}
	return fetchDetail(ctx, client, request, sleep)
}
