package boundedhttp

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"time"
)

type ErrorKind string

const (
	ErrorConfig         ErrorKind = "config"
	ErrorRequestLimit   ErrorKind = "request_limit"
	ErrorBodyLimit      ErrorKind = "body_limit"
	ErrorAggregateLimit ErrorKind = "aggregate_limit"
	ErrorCanceled       ErrorKind = "canceled"
	ErrorTimeout        ErrorKind = "timeout"
	ErrorTransport      ErrorKind = "transport"
)

type Error struct {
	Kind  ErrorKind
	URL   string
	Limit int64
	Err   error
}

func (e *Error) Error() string {
	if e.Limit > 0 {
		return fmt.Sprintf("bounded HTTP %s for %s (limit=%d)", e.Kind, e.URL, e.Limit)
	}
	return fmt.Sprintf("bounded HTTP %s for %s", e.Kind, e.URL)
}

func (e *Error) Unwrap() error { return e.Err }

func newError(kind ErrorKind, rawURL string, limit int64, err error) *Error {
	return &Error{Kind: kind, URL: sanitizedURL(rawURL), Limit: limit, Err: err}
}

func sanitizedURL(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "<invalid-url>"
	}
	parsed.User = nil
	parsed.RawQuery = ""
	parsed.ForceQuery = false
	parsed.Fragment = ""
	return parsed.String()
}

type Config struct {
	RequestTimeout           time.Duration
	MaxDecodedBodyBytes      int64
	MaxRequests              int
	MaxAggregateDecodedBytes int64
}

type Client struct {
	httpClient *http.Client
	config     Config
}

type Stats struct {
	Requests     int
	DecodedBytes int64
}

type Session struct {
	client *Client
	stats  Stats
}

type Response struct {
	StatusCode int
	Header     http.Header
	Body       []byte
}

func New(config Config) (*Client, error) {
	if config.RequestTimeout <= 0 || config.MaxDecodedBodyBytes <= 0 || config.MaxDecodedBodyBytes == math.MaxInt64 || config.MaxRequests <= 0 || config.MaxAggregateDecodedBytes <= 0 || config.MaxAggregateDecodedBytes == math.MaxInt64 {
		return nil, &Error{Kind: ErrorConfig}
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// Phase 0 counts every RoundTrip as one wire request. net/http may
	// transparently retry an idempotent GET on a stale reused connection, so
	// connection reuse is disabled until an attempt-aware production transport
	// can account for retries at the wire boundary. Pinning TLS ALPN to HTTP/1.1
	// and the explicit empty map both prevent HTTP/2 negotiation for this
	// deliberately conservative pilot transport.
	transport.DisableKeepAlives = true
	transport.ForceAttemptHTTP2 = false
	transport.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
	forceHTTP1ALPN(transport)
	return &Client{
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   config.RequestTimeout,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				// Redirects are outside the explicit-URL pilot cohort. Returning the
				// response keeps MaxRequests equal to actual origin requests.
				return http.ErrUseLastResponse
			},
		},
		config: config,
	}, nil
}

func forceHTTP1ALPN(transport *http.Transport) {
	tlsConfig := transport.TLSClientConfig
	if tlsConfig == nil {
		tlsConfig = &tls.Config{}
	} else {
		tlsConfig = tlsConfig.Clone()
	}
	tlsConfig.NextProtos = []string{"http/1.1"}
	transport.TLSClientConfig = tlsConfig
}

func (c *Client) NewSession() *Session { return &Session{client: c} }

// Stats returns counters for this session. A Session is deliberately
// single-goroutine; one sitemap traversal owns it from start to finish.
func (s *Session) Stats() Stats { return s.stats }

func (s *Session) Get(ctx context.Context, rawURL string, headers http.Header) (Response, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	if s.stats.Requests >= s.client.config.MaxRequests {
		return Response{}, newError(ErrorRequestLimit, rawURL, int64(s.client.config.MaxRequests), nil)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	req.Header = headers.Clone()
	s.stats.Requests++
	resp, err := s.client.httpClient.Do(req)
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return Response{}, newError(ErrorCanceled, rawURL, 0, context.Canceled)
		}
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return Response{}, newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
		}
		var netErr interface{ Timeout() bool }
		if errors.As(err, &netErr) && netErr.Timeout() {
			return Response{}, newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
		}
		return Response{}, newError(ErrorTransport, rawURL, 0, errors.New("request failed"))
	}
	defer resp.Body.Close()
	response := Response{StatusCode: resp.StatusCode, Header: resp.Header.Clone()}
	if resp.StatusCode != http.StatusOK {
		// Error bodies are neither monitor input nor useful accounting. Do not
		// let an oversized CDN/WAF page hide the typed response status.
		return response, nil
	}

	aggregateRemaining := s.client.config.MaxAggregateDecodedBytes - s.stats.DecodedBytes
	readLimit := min(s.client.config.MaxDecodedBodyBytes, aggregateRemaining) + 1
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, readLimit))
	s.stats.DecodedBytes += int64(len(body))
	if readErr != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return response, newError(ErrorCanceled, rawURL, 0, context.Canceled)
		}
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return response, newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
		}
		var netErr interface{ Timeout() bool }
		if errors.As(readErr, &netErr) && netErr.Timeout() {
			return response, newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
		}
		return response, newError(ErrorTransport, rawURL, 0, errors.New("response read failed"))
	}
	if int64(len(body)) > aggregateRemaining {
		return response, newError(ErrorAggregateLimit, rawURL, s.client.config.MaxAggregateDecodedBytes, nil)
	}
	if int64(len(body)) > s.client.config.MaxDecodedBodyBytes {
		return response, newError(ErrorBodyLimit, rawURL, s.client.config.MaxDecodedBodyBytes, nil)
	}

	response.Body = body
	return response, nil
}
