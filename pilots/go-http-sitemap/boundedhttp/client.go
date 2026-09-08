package boundedhttp

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strings"
	"sync"
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
	// SharedTransport opts all sessions created by this Client into one bounded,
	// process-owned connection pool. Nil preserves the original behavior in
	// which every Session owns an isolated pool with the existing defaults.
	SharedTransport *SharedTransportConfig
}

// SharedTransportConfig makes every shared-pool resource bound explicit. A
// zero-valued field is rejected rather than inheriting net/http defaults,
// because MaxConnsPerHost defaults to no limit.
type SharedTransportConfig struct {
	MaxIdleConns        int
	MaxIdleConnsPerHost int
	MaxConnsPerHost     int
	// MaxConnections bounds transport-visible connections and in-progress dial
	// calls across every origin. A dual-stack DialContext may transiently race
	// underlying Happy-Eyeballs sockets within one admitted dial; the hermetic
	// benchmark uses single-address origins. MaxConcurrentRequests separately
	// bounds requests holding response bodies or draining status responses.
	MaxConnections        int
	MaxConcurrentRequests int
	IdleConnTimeout       time.Duration
}

type Client struct {
	transport       *http.Transport
	config          Config
	sharedTransport bool
	requestPermits  chan struct{}
	connections     *connectionLimiter
	hosts           *hostLimiterSet
}

type hostLimiterSet struct {
	mu    sync.Mutex
	limit int
	gates map[string]*hostLimiter
}

type hostLimiter struct {
	permits chan struct{}
	refs    int
}

type connectionLimiter struct {
	permits       chan struct{}
	permitsInUse  atomic.Int64
	maxPermitsUse atomic.Int64
	waiters       atomic.Int64
	open          atomic.Int64
	maxOpen       atomic.Int64
	closeIdle     func()
}

type limitedConn struct {
	net.Conn
	once    sync.Once
	release func()
}

func (c *limitedConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(c.release)
	return err
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

// ConnectionStats reports the shared transport's client-local resource state.
// InUsePermits includes established connections and in-progress dials. The
// maxima cover the lifetime of this Client, including any warmup batch.
type ConnectionStats struct {
	Open                int64 `json:"open"`
	MaximumOpen         int64 `json:"maximum_open"`
	InUsePermits        int64 `json:"in_use_permits"`
	MaximumInUsePermits int64 `json:"maximum_in_use_permits"`
	PermitLimit         int64 `json:"permit_limit"`
	Waiters             int64 `json:"waiters"`
}

type Session struct {
	client        *Client
	httpClient    *http.Client
	transport     *http.Transport
	ownsTransport bool
	stats         Stats
	wireAttempts  atomic.Int64
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
	if shared := config.SharedTransport; shared != nil {
		if shared.MaxIdleConns <= 0 || shared.MaxIdleConnsPerHost <= 0 || shared.MaxConnsPerHost <= 0 || shared.MaxConnections <= 0 || shared.MaxConcurrentRequests <= 0 || shared.IdleConnTimeout <= 0 || shared.MaxIdleConnsPerHost > shared.MaxIdleConns || shared.MaxIdleConnsPerHost > shared.MaxConnsPerHost || shared.MaxIdleConns > shared.MaxConnections || shared.MaxConnsPerHost > shared.MaxConnections {
			return nil, &Error{Kind: ErrorConfig}
		}
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
	client := &Client{
		transport:       transport,
		config:          config,
		sharedTransport: config.SharedTransport != nil,
	}
	if shared := config.SharedTransport; shared != nil {
		transport.MaxIdleConns = shared.MaxIdleConns
		transport.MaxIdleConnsPerHost = shared.MaxIdleConnsPerHost
		transport.MaxConnsPerHost = shared.MaxConnsPerHost
		transport.IdleConnTimeout = shared.IdleConnTimeout
		limiter := &connectionLimiter{permits: make(chan struct{}, shared.MaxConnections)}
		limiter.closeIdle = transport.CloseIdleConnections
		baseDial := transport.DialContext
		if baseDial == nil {
			baseDial = (&net.Dialer{}).DialContext
		}
		transport.DialContext = limiter.wrapDialContext(baseDial)
		client.connections = limiter
		client.requestPermits = make(chan struct{}, shared.MaxConcurrentRequests)
		client.hosts = &hostLimiterSet{
			limit: shared.MaxConnsPerHost,
			gates: make(map[string]*hostLimiter),
		}
	}
	return client, nil
}

func (s *hostLimiterSet) acquire(ctx context.Context, origin string) (func(), error) {
	s.mu.Lock()
	gate := s.gates[origin]
	if gate == nil {
		gate = &hostLimiter{permits: make(chan struct{}, s.limit)}
		s.gates[origin] = gate
	}
	gate.refs++
	s.mu.Unlock()

	select {
	case gate.permits <- struct{}{}:
		if err := ctx.Err(); err != nil {
			<-gate.permits
			s.releaseRef(origin, gate)
			return nil, err
		}
	case <-ctx.Done():
		s.releaseRef(origin, gate)
		return nil, ctx.Err()
	}

	var once sync.Once
	return func() {
		once.Do(func() {
			<-gate.permits
			s.releaseRef(origin, gate)
		})
	}, nil
}

func (s *hostLimiterSet) releaseRef(origin string, gate *hostLimiter) {
	s.mu.Lock()
	defer s.mu.Unlock()
	gate.refs--
	if gate.refs == 0 {
		delete(s.gates, origin)
	}
}

func (s *hostLimiterSet) size() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.gates)
}

func (s *hostLimiterSet) refs(origin string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	if gate := s.gates[origin]; gate != nil {
		return gate.refs
	}
	return 0
}

func (l *connectionLimiter) wrapDialContext(baseDial func(context.Context, string, string) (net.Conn, error)) func(context.Context, string, string) (net.Conn, error) {
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		select {
		case l.permits <- struct{}{}:
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
			l.waiters.Add(1)
			// net/http does not evict an idle connection for one origin merely
			// because another origin needs a new connection. Proactively close
			// shared idle sockets before waiting so a saturated global cap cannot
			// deadlock on stale-origin idle capacity.
			l.closeIdle()
			select {
			case l.permits <- struct{}{}:
				l.waiters.Add(-1)
			case <-ctx.Done():
				l.waiters.Add(-1)
				return nil, ctx.Err()
			}
		}
		permitsInUse := l.permitsInUse.Add(1)
		storeAtomicMax(&l.maxPermitsUse, permitsInUse)

		conn, err := baseDial(ctx, network, address)
		if err != nil {
			if conn != nil {
				_ = conn.Close()
			}
			l.permitsInUse.Add(-1)
			<-l.permits
			return nil, err
		}
		current := l.open.Add(1)
		storeAtomicMax(&l.maxOpen, current)
		return &limitedConn{
			Conn: conn,
			release: func() {
				l.open.Add(-1)
				l.permitsInUse.Add(-1)
				<-l.permits
			},
		}, nil
	}
}

func (l *connectionLimiter) evictIdleForWaiters() {
	if l != nil && l.waiters.Load() > 0 {
		// This also covers connections that were active when a waiter first
		// called CloseIdleConnections and became idle only afterward.
		l.closeIdle()
	}
}

func storeAtomicMax(target *atomic.Int64, candidate int64) {
	for current := target.Load(); candidate > current; current = target.Load() {
		if target.CompareAndSwap(current, candidate) {
			return
		}
	}
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
	// By default a sitemap invocation gets its own pool. Retries and child
	// sitemap GETs can reuse connections, while stale state cannot leak across
	// invocations. Concurrent-worker callers may explicitly opt into the
	// Client-owned shared pool instead.
	transport := c.transport
	ownsTransport := false
	if !c.sharedTransport {
		transport = c.transport.Clone()
		ownsTransport = true
	}
	return &Session{
		client:        c,
		transport:     transport,
		ownsTransport: ownsTransport,
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

// Close releases idle connections owned by this invocation's pool. It is a
// no-op for a shared pool because ownership remains with Client.Close.
func (s *Session) Close() {
	if s.ownsTransport {
		s.transport.CloseIdleConnections()
	}
}

// Close releases idle connections owned by the Client. It is primarily
// meaningful when SharedTransport is enabled. CloseIdleConnections is safe to
// call concurrently with active requests; it neither interrupts them nor
// prevents later sessions from opening new connections.
func (c *Client) Close() { c.transport.CloseIdleConnections() }

// ConnectionStats returns zeros when the Client does not use a shared
// transport. Shared clients expose exact local high-water counters without
// consulting peer-observed TCP state.
func (c *Client) ConnectionStats() ConnectionStats {
	if c.connections == nil {
		return ConnectionStats{}
	}
	return ConnectionStats{
		Open:                c.connections.open.Load(),
		MaximumOpen:         c.connections.maxOpen.Load(),
		InUsePermits:        c.connections.permitsInUse.Load(),
		MaximumInUsePermits: c.connections.maxPermitsUse.Load(),
		PermitLimit:         int64(cap(c.connections.permits)),
		Waiters:             c.connections.waiters.Load(),
	}
}

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
	if ctx == nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return Response{}, newError(ErrorConfig, rawURL, 0, nil)
	}
	if s.stats.Requests >= s.client.config.MaxRequests {
		return Response{}, newError(ErrorRequestLimit, rawURL, int64(s.client.config.MaxRequests), nil)
	}
	if permits := s.client.requestPermits; permits != nil {
		if err := ctx.Err(); err != nil {
			return Response{}, contextError(rawURL, err)
		}
		releaseHost, err := s.client.hosts.acquire(ctx, canonicalOrigin(parsed))
		if err != nil {
			return Response{}, contextError(rawURL, err)
		}
		// This defer is registered before the global request release below, so
		// cross-origin connection waiters can evict the completed request's idle
		// socket before another same-origin request enters net/http.
		defer releaseHost()
		select {
		case permits <- struct{}{}:
		case <-ctx.Done():
			return Response{}, contextError(rawURL, ctx.Err())
		}
		if err := ctx.Err(); err != nil {
			<-permits
			return Response{}, contextError(rawURL, err)
		}
		defer func() {
			<-permits
			s.client.connections.evictIdleForWaiters()
		}()
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

func canonicalOrigin(parsed *url.URL) string {
	hostname := strings.ToLower(parsed.Hostname())
	port := parsed.Port()
	if (parsed.Scheme == "http" && port == "80") || (parsed.Scheme == "https" && port == "443") {
		port = ""
	}
	host := hostname
	if port != "" {
		host = net.JoinHostPort(hostname, port)
	} else if strings.Contains(hostname, ":") {
		host = "[" + hostname + "]"
	}
	return parsed.Scheme + "://" + host
}

func contextError(rawURL string, err error) error {
	if errors.Is(err, context.DeadlineExceeded) {
		return newError(ErrorTimeout, rawURL, 0, context.DeadlineExceeded)
	}
	return newError(ErrorCanceled, rawURL, 0, context.Canceled)
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
