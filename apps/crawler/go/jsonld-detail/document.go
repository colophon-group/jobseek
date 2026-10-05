package jsonld

// Public document transport shared by native extraction runtimes. It does not
// parse job content, retry empty content, or follow provider-specific iframes.
import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type DocumentOptions struct {
	Headers       map[string]string `json:"headers"`
	RetryLimits   map[int]int       `json:"retry_limits"`
	SameOrigin    bool              `json:"same_origin"`
	PublicHeaders bool              `json:"public_headers"`
}
type DocumentResult struct {
	Requests    int    `json:"requests"`
	Responses   int    `json:"responses"`
	Bytes       int    `json:"bytes"`
	Status      int    `json:"status"`
	FinalURL    string `json:"final_url"`
	ContentType string `json:"content_type"`
	Body        []byte `json:"body_base64"`
	ErrorKind   string `json:"error_kind,omitempty"`
	TDMSource   string `json:"tdm_source,omitempty"`
	TDMPolicy   string `json:"tdm_policy,omitempty"`
	Error       string `json:"error,omitempty"`
}

// ClientFactory is not configurable by publisher data; DNS/IP checks are
// performed by the existing verified public dialer on every connection.
func NewPublicClient() *http.Client            { return newClient() }
func ValidPublicEndpoint(endpoint string) bool { return validEndpoint(endpoint) }

func origin(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return ""
	}
	port := u.Port()
	if port == "" {
		if u.Scheme == "https" {
			port = "443"
		} else {
			port = "80"
		}
	}
	return strings.ToLower(u.Scheme) + "://" + strings.ToLower(u.Hostname()) + ":" + port
}
func documentPage(ctx context.Context, client requestDoer, endpoint string, opts DocumentOptions, stats *DocumentResult) ([]byte, error) {
	expected := origin(endpoint)
	maxHops := 20
	if opts.PublicHeaders {
		maxHops = 5
		opts.SameOrigin = true
	}
	visited := map[string]bool{}
	for hop := 0; hop <= maxHops; hop++ {
		if !validEndpoint(endpoint) || len(endpoint) > 8192 {
			return nil, errors.New("invalid public document endpoint")
		}
		u, _ := url.Parse(endpoint)
		u.Fragment = ""
		endpoint = u.String()
		if opts.SameOrigin && origin(endpoint) != expected {
			return nil, errors.New("redirect left original origin")
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", accept)
		for k, v := range opts.Headers {
			request.Header.Set(k, v)
		}
		// Match URL+cookie-state loop detection. Set-Cookie handshakes may revisit
		// the original URL; a URL-only visited set would reject legitimate work.
		if opts.SameOrigin && !opts.PublicHeaders {
			state := ""
			if c, ok := client.(*http.Client); ok && c.Jar != nil {
				for _, cookie := range c.Jar.Cookies(u) {
					state += cookie.String() + "; "
				}
			}
			key := endpoint + fmt.Sprintf("/%x", sha256.Sum256([]byte(state)))
			if visited[key] {
				return nil, errors.New("same-origin redirect loop")
			}
			visited[key] = true
		}
		stats.Requests++
		response, err := client.Do(request)
		if err != nil {
			return nil, err
		}
		stats.Responses++
		stats.Status = response.StatusCode
		stats.FinalURL = endpoint
		stats.ContentType = response.Header.Get("Content-Type")
		if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
			response.Body.Close()
			stats.ErrorKind = "tdm"
			stats.TDMSource = "header"
			stats.TDMPolicy = response.Header.Get("TDM-Policy")
			return nil, errors.New("tdm-reservation=1")
		}
		body, err := io.ReadAll(io.LimitReader(&readIdleBody{ReadCloser: response.Body, timeout: 30 * time.Second}, maxResponseBytes+1))
		response.Body.Close()
		stats.Bytes += len(body)
		if err != nil {
			return nil, err
		}
		if len(body) > maxResponseBytes {
			return nil, errors.New("public document exceeds 16 MiB")
		}
		reservation, policy := tdmMetadata(decodedBody(body, stats.ContentType))
		if reservation == "1" {
			stats.ErrorKind = "tdm"
			stats.TDMSource = "meta"
			stats.TDMPolicy = response.Header.Get("TDM-Policy")
			if policy != "" {
				stats.TDMPolicy = policy
			}
			return nil, errors.New("tdm-reservation=1")
		}
		status := response.StatusCode
		redirect := status == 301 || status == 302 || status == 303 || status == 307 || status == 308
		locations := response.Header.Values("Location")
		if opts.SameOrigin && status >= 300 && status < 400 {
			if !redirect {
				return nil, errors.New("unsupported same-origin redirect status")
			}
			if len(locations) != 1 || strings.TrimSpace(locations[0]) == "" {
				return nil, errors.New("same-origin redirect requires one Location")
			}
		}
		if redirect && len(locations) > 0 && response.Header.Get("Location") != "" {
			if hop == maxHops {
				return nil, errors.New("public document redirect limit")
			}
			target, err := response.Location()
			if err != nil {
				return nil, err
			}
			endpoint = target.String()
			continue
		}
		return body, nil
	}
	return nil, errors.New("public document redirect limit")
}
func validateDocumentOptions(opts DocumentOptions) error {
	if len(opts.Headers) > 0 && !opts.PublicHeaders {
		return errors.New("configured public headers require the public redirect contract")
	}
	allowed := map[string]bool{"accept": true, "accept-language": true, "cache-control": true, "pragma": true, "user-agent": true, "x-return-format": true}
	seen := map[string]bool{}
	for k, v := range opts.Headers {
		key := strings.ToLower(strings.TrimSpace(k))
		if !allowed[key] || seen[key] || len(k) > 64 || len(v) > 1024 || strings.TrimSpace(v) == "" {
			return errors.New("invalid public request header")
		}
		seen[key] = true
		for _, c := range v {
			if c != '\t' && (c < 32 || c > 126) {
				return errors.New("invalid public request header")
			}
		}
	}
	for status, limit := range opts.RetryLimits {
		if status < 400 || status > 599 || limit < 0 || limit > 5 {
			return errors.New("invalid public status retry limit")
		}
	}
	return nil
}
func fetchDocument(ctx context.Context, client requestDoer, endpoint string, opts DocumentOptions, wait sleepFunc) (DocumentResult, error) {
	result := DocumentResult{}
	if err := validateDocumentOptions(opts); err != nil {
		result.ErrorKind = "config"
		return result, err
	}
	// Configured public headers use the existing five-hop public_get contract:
	// no status retry, even when retry_statuses are present in board config.
	if opts.PublicHeaders {
		opts.RetryLimits = nil
		if c, ok := client.(*http.Client); ok {
			c.Jar = nil
		}
	}
	used := map[int]int{}
	total := 0
	for {
		body, err := documentPage(ctx, client, endpoint, opts, &result)
		if err != nil {
			return result, err
		}
		status := result.Status
		if used[status] >= opts.RetryLimits[status] {
			result.Body = body
			return result, nil
		}
		used[status]++
		total++
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(total-1)) * (0.5 + rand.Float64()))
		if err := wait(ctx, delay); err != nil {
			return result, err
		}
	}
}
func FetchDocument(ctx context.Context, endpoint string, opts DocumentOptions) (DocumentResult, error) {
	client := NewPublicClient()
	defer client.CloseIdleConnections()
	return fetchDocument(ctx, client, endpoint, opts, sleep)
}

// FetchDocumentWithClient preserves the public document contract when a native
// worker supplies its sealed, observed transport. Redirects remain explicit.
func FetchDocumentWithClient(ctx context.Context, endpoint string, opts DocumentOptions, client *http.Client) (DocumentResult, error) {
	if client == nil {
		return DocumentResult{}, errors.New("missing public document client")
	}
	return fetchDocument(ctx, client, endpoint, opts, sleep)
}

func ValidateDocumentOptions(opts DocumentOptions) error { return validateDocumentOptions(opts) }

// DecodeDocument matches the existing HTTP response charset decoding, including
// replacement of malformed UTF-8. It does not infer HTML meta charsets.
func DecodeDocument(body []byte, contentType string) string {
	return strings.ToValidUTF8(string(decodedBody(body, contentType)), "\ufffd")
}

func IsAvatureDetailURL(endpoint string) bool { return avatureDetail(endpoint) }
