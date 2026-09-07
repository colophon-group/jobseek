package boundedhttp

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"sync/atomic"
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
	transport *http.Transport
	config    Config
}

type Stats struct {
	// Requests is the number of explicit GET attempts admitted against the
	// configured cap. WireAttempts is the subset that reached net/http's
	// WroteRequest hook; it can be lower when dialing or TLS setup fails, but
	// must never be higher because transparent transport retries are disabled.
	Requests        int
	WireAttempts    int
	DecodedBytes    int64
	StatusBodyBytes int64
}

type Session struct {
	client       *Client
	httpClient   *http.Client
	transport    *http.Transport
	stats        Stats
	wireAttempts atomic.Int64
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
	// Keep the pilot on HTTP/1.1. In addition to making the connection-aware
	// parity fixture deterministic, this excludes HTTP/2's separate retry path.
	// Get supplies a non-replayable, empty request body so net/http cannot
	// transparently retry a GET after selecting a stale pooled connection.
	transport.DisableKeepAlives = false
	transport.ForceAttemptHTTP2 = false
	transport.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
	forceHTTP1ALPN(transport)
	return &Client{
		transport: transport,
		config:    config,
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

func (c *Client) NewSession() *Session {
	// A sitemap invocation gets its own pool. Retries and child sitemap GETs
	// can reuse connections, while stale state cannot leak across invocations.
	transport := c.transport.Clone()
	return &Session{
		client:    c,
		transport: transport,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   c.config.RequestTimeout,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				// Redirects are outside the explicit-URL pilot cohort. Returning the
				// response keeps the request cap equal to admitted GET attempts.
				return http.ErrUseLastResponse
			},
		},
	}
}

// Close releases idle connections owned by this invocation's pool.
func (s *Session) Close() { s.transport.CloseIdleConnections() }

// Stats returns counters for this session. A Session is deliberately
// single-goroutine; one sitemap traversal owns it from start to finish.
func (s *Session) Stats() Stats {
	stats := s.stats
	stats.WireAttempts = int(s.wireAttempts.Load())
	return stats
}

// nonReplayableEmptyBody is empty on the wire but intentionally differs from
// http.NoBody and has no GetBody function. For HTTP/1, those properties make a
// GET ineligible for net/http's transparent retry on a stale pooled
// connection. transferWriter probes it, observes EOF, and emits a normal
// bodyless GET (no chunked body or Content-Length header).
type nonReplayableEmptyBody struct{}

func (*nonReplayableEmptyBody) Read([]byte) (int, error) { return 0, io.EOF }
func (*nonReplayableEmptyBody) Close() error             { return nil }

func (s *Session) Get(ctx context.Context, rawURL string, headers http.Header) (Response, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	if s.stats.Requests >= s.client.config.MaxRequests {
		return Response{}, newError(ErrorRequestLimit, rawURL, int64(s.client.config.MaxRequests), nil)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, &nonReplayableEmptyBody{})
	if err != nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	req.Header = headers.Clone()
	req = req.WithContext(httptrace.WithClientTrace(req.Context(), &httptrace.ClientTrace{
		WroteRequest: func(httptrace.WroteRequestInfo) {
			s.wireAttempts.Add(1)
		},
	}))
	s.stats.Requests++
	resp, err := s.httpClient.Do(req)
	if s.wireAttempts.Load() > int64(s.stats.Requests) {
		if resp != nil {
			_ = resp.Body.Close()
		}
		return Response{}, newError(ErrorRequestLimit, rawURL, int64(s.client.config.MaxRequests), errors.New("transport replayed request"))
	}
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
		// Drain only within the existing decoded-byte bounds. A complete drain
		// makes this HTTP/1 connection reusable; a larger response is closed and
		// discarded without hiding the typed status from the sitemap layer.
		if err := s.drainStatusBody(ctx, rawURL, resp.Body); err != nil {
			return response, err
		}
		return response, nil
	}

	aggregateRemaining := s.aggregateRemaining()
	if aggregateRemaining <= 0 {
		return response, newError(ErrorAggregateLimit, rawURL, s.client.config.MaxAggregateDecodedBytes, nil)
	}
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

func (s *Session) aggregateRemaining() int64 {
	return s.client.config.MaxAggregateDecodedBytes - s.stats.DecodedBytes - s.stats.StatusBodyBytes
}

func (s *Session) drainStatusBody(ctx context.Context, rawURL string, body io.Reader) error {
	limit := min(s.client.config.MaxDecodedBodyBytes, s.aggregateRemaining())
	if limit <= 0 {
		return nil
	}
	read, err := io.Copy(io.Discard, io.LimitReader(body, limit))
	s.stats.StatusBodyBytes += read
	if errors.Is(ctx.Err(), context.Canceled) {
		return newError(ErrorCanceled, rawURL, 0, context.Canceled)
	}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
	}
	var netErr interface{ Timeout() bool }
	if errors.As(err, &netErr) && netErr.Timeout() {
		return newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
	}
	// A generic error while opportunistically draining an error response does
	// not hide the already-received HTTP status. Closing the body evicts the
	// unusable connection from this session's pool.
	return nil
}
