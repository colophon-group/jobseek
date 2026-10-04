package worker

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/netip"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/idna"
)

const directOperationTimeout = 30 * time.Second

type DirectHTTPConfig struct {
	// Supply the pinned deployment CA bundle. No system-store or insecure
	// fallback is substituted for the Python client's certifi trust roots.
	CABundlePEM []byte
	// Frozen trusted startup allowlist, never board/probe data. The eventual
	// runtime must derive it from protected operator/deployment configuration.
	InternalHosts []string
	// Workday's API needs HTTP/2 negotiation. This is a compiled runtime
	// transport choice; board metadata cannot change trust or proxy policy.
	EnableHTTP2 bool
}

// VerifiedDirectHTTP seals the process-owned client used by the native claim
// runner. Callers cannot swap its transport, redirect policy or cookie jar.
// CA/internal-host inputs still require protected installed startup admission.
type VerifiedDirectHTTP struct{ client *http.Client }

func NewVerifiedDirectHTTP(config DirectHTTPConfig) (*VerifiedDirectHTTP, error) {
	client, err := NewDirectHTTP(config)
	if err != nil {
		return nil, err
	}
	return &VerifiedDirectHTTP{client: client}, nil
}

func (c *VerifiedDirectHTTP) CloseIdleConnections() {
	if c != nil && c.client != nil {
		c.client.CloseIdleConnections()
	}
}

// NewDirectHTTP creates one reusable verified client, defaulting to HTTP/1.1. Request contexts
// bound the whole task; network operations retain separate 30-second limits.
// This is transport, not startup/claim/host-circuit or database authority.
func NewDirectHTTP(config DirectHTTPConfig) (*http.Client, error) {
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(config.CABundlePEM) {
		return nil, errors.New("direct HTTP CA bundle unavailable")
	}
	allowed := make(map[string]bool)
	for _, host := range config.InternalHosts {
		normalized, err := directHost(host)
		if err != nil || normalized == "" || strings.ContainsAny(normalized, "/@\r\n\x00") {
			return nil, errors.New("invalid direct HTTP internal host")
		}
		allowed[normalized] = true
	}
	transport := &directTransport{allowed: allowed, timeout: directOperationTimeout, requests: make(chan struct{}, 100), connections: make(chan struct{}, 100), lookup: func(ctx context.Context, host string) ([]netip.Addr, error) {
		return net.DefaultResolver.LookupNetIP(ctx, "ip", host)
	}}
	transport.dial = (&net.Dialer{Timeout: directOperationTimeout, KeepAlive: 30 * time.Second}).DialContext
	transport.inner = &http.Transport{
		Proxy: nil, DialContext: transport.dialContext, ForceAttemptHTTP2: false,
		TLSClientConfig:     &tls.Config{RootCAs: roots, SessionTicketsDisabled: true, NextProtos: []string{"http/1.1"}},
		TLSNextProto:        map[string]func(string, *tls.Conn) http.RoundTripper{},
		TLSHandshakeTimeout: directOperationTimeout, MaxConnsPerHost: 100,
		MaxIdleConns: 20, MaxIdleConnsPerHost: 20, IdleConnTimeout: 5 * time.Second,
		DisableCompression: true,
	}
	if config.EnableHTTP2 {
		transport.inner.ForceAttemptHTTP2 = true
		transport.inner.TLSNextProto = nil
		transport.inner.TLSClientConfig.NextProtos = []string{"h2", "http/1.1"}
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, errors.New("direct HTTP cookie jar unavailable")
	}
	return &http.Client{Transport: transport, Jar: jar, CheckRedirect: func(_ *http.Request, via []*http.Request) error {
		if len(via) > 20 {
			return errors.New("direct HTTP redirect limit")
		}
		return nil
	}}, nil
}

type directTransport struct {
	inner                 *http.Transport
	allowed               map[string]bool
	lookup                lookupIPFunc
	dial                  func(context.Context, string, string) (net.Conn, error)
	timeout               time.Duration
	requests, connections chan struct{}
}

type pinnedTarget struct {
	host      string
	addresses []netip.Addr
}
type targetContextKey struct{}

func (t *directTransport) validate(ctx context.Context, host string) (pinnedTarget, error) {
	target := pinnedTarget{host: host}
	if t.allowed[host] {
		return target, nil
	}
	if address, err := netip.ParseAddr(host); err == nil {
		if blockedAddress(address) {
			return target, ErrUnsafeURL
		}
		target.addresses = []netip.Addr{address}
		return target, nil
	}
	resolve, cancel := context.WithTimeout(ctx, t.timeout)
	defer cancel()
	var err error
	target.addresses, err = resolvePublic(resolve, host, t.lookup)
	return target, err
}

func (t *directTransport) RoundTrip(original *http.Request) (*http.Response, error) {
	if original.URL == nil || (original.URL.Scheme != "http" && original.URL.Scheme != "https") || original.URL.Hostname() == "" {
		return nil, ErrUnsafeURL
	}
	host, err := directHost(original.URL.Hostname())
	if err != nil {
		return nil, ErrUnsafeURL
	}
	target, err := t.validate(original.Context(), host)
	if err != nil {
		return nil, err // refused targets never enter origin/failure accounting
	}
	observation := httpObservation(original.Context())
	observation.noteRequest(host)
	pool, cancel := context.WithTimeout(original.Context(), t.timeout)
	defer cancel()
	select {
	case t.requests <- struct{}{}:
	case <-pool.Done():
		observation.noteFailure(host, pool.Err())
		return nil, pool.Err()
	}
	release := sync.OnceFunc(func() { <-t.requests })
	request := original.Clone(context.WithValue(original.Context(), targetContextKey{}, target))
	if !hasHTTPHeader(request.Header, "User-Agent") {
		request.Header.Set("User-Agent", ordinaryUserAgent)
	}
	if !hasHTTPHeader(request.Header, "Accept") {
		request.Header.Set("Accept", ordinaryAccept)
	}
	if !hasHTTPHeader(request.Header, "Accept-Encoding") {
		request.Header.Set("Accept-Encoding", "gzip, deflate")
	}
	if !hasHTTPHeader(request.Header, "Connection") {
		request.Header.Set("Connection", "keep-alive")
	}
	// net/http retries replayable GETs on failed reused connections. A private
	// non-rewindable empty body disables that branch without sending a body,
	// chunked encoding or Content-Length on GET. Redirects still use the
	// original request; no public method/header/idempotency value is changed.
	request.GetBody = nil
	if request.Body == nil || request.Body == http.NoBody {
		request.Body = &nonReplayableEmptyBody{}
		request.ContentLength = 0
	}
	response, err := t.inner.RoundTrip(request)
	if err != nil {
		release()
		observation.noteFailure(host, err)
		return nil, err
	}
	observation.noteResponse(host, response.StatusCode)
	raw := &observedBody{ReadCloser: response.Body, observation: observation, release: release}
	response.Body = newDecodedBody(raw, response.Header.Get("Content-Encoding"))
	return response, nil
}

func hasHTTPHeader(headers http.Header, name string) bool {
	for key := range headers {
		if strings.EqualFold(key, name) {
			return true
		}
	}
	return false
}

func directHost(host string) (string, error) {
	host = strings.ToLower(host)
	if address, err := netip.ParseAddr(host); err == nil {
		return address.String(), nil
	}
	return idna.Lookup.ToASCII(host)
}

func (t *directTransport) CloseIdleConnections() { t.inner.CloseIdleConnections() }

func (t *directTransport) dialContext(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return nil, ErrUnsafeURL
	}
	target, ok := ctx.Value(targetContextKey{}).(pinnedTarget)
	if !ok || !strings.EqualFold(host, target.host) {
		return nil, ErrUnsafeURL
	}
	connect, cancel := context.WithTimeout(ctx, t.timeout)
	defer cancel()
	select {
	case t.connections <- struct{}{}:
	default:
		// Evict idle connections to other origins before waiting for this
		// pool slot. CloseIdleConnections also closes newly idle connections;
		// neither active requests nor the total connection budget are enlarged.
		t.inner.CloseIdleConnections()
		select {
		case t.connections <- struct{}{}:
		case <-connect.Done():
			return nil, connect.Err()
		}
	}
	release := sync.OnceFunc(func() { <-t.connections })
	var connection net.Conn
	if len(target.addresses) == 0 {
		// Only an explicitly allowed startup host can enter ordinary DNS here.
		connection, err = t.dial(connect, network, address)
	} else {
		connection, err = t.dialPublic(connect, network, port, target.addresses)
	}
	if err != nil {
		release()
		return nil, err
	}
	return &operationConn{Conn: connection, timeout: t.timeout, release: release}, nil
}

// All candidates were validated together, and every attempted socket uses a
// captured literal. Staggered fallback avoids an unreachable first IPv6 answer
// suppressing healthy IPv4. Losing sockets close before the dial returns.
func (t *directTransport) dialPublic(ctx context.Context, network, port string, addresses []netip.Addr) (net.Conn, error) {
	race, cancel := context.WithCancel(ctx)
	defer cancel()
	type outcome struct {
		connection net.Conn
		err        error
	}
	results := make(chan outcome, len(addresses))
	for i, ip := range addresses {
		go func() {
			if i != 0 {
				timer := time.NewTimer(time.Duration(i) * 250 * time.Millisecond)
				defer timer.Stop()
				select {
				case <-race.Done():
					results <- outcome{err: race.Err()}
					return
				case <-timer.C:
				}
			}
			connection, err := t.dial(race, network, net.JoinHostPort(ip.String(), port))
			results <- outcome{connection, err}
		}()
	}
	var winner net.Conn
	var failure error
	for range addresses {
		result := <-results
		if result.connection != nil {
			if winner == nil {
				winner = result.connection
				cancel()
			} else {
				_ = result.connection.Close()
			}
		} else {
			failure = result.err
		}
	}
	if winner != nil {
		return winner, nil
	}
	return nil, failure
}

type operationConn struct {
	net.Conn
	timeout time.Duration
	release func()
}

func (c *operationConn) Read(p []byte) (int, error) {
	if err := c.Conn.SetReadDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Read(p)
}
func (c *operationConn) Write(p []byte) (int, error) {
	if err := c.Conn.SetWriteDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Write(p)
}
func (c *operationConn) Close() error {
	err := c.Conn.Close()
	c.release()
	return err
}

type nonReplayableEmptyBody struct{}

func (*nonReplayableEmptyBody) Read([]byte) (int, error) { return 0, io.EOF }
func (*nonReplayableEmptyBody) Close() error             { return nil }

type observedBody struct {
	io.ReadCloser
	observation *HTTPObservation
	release     func()
	readError   error
	bytes       int64
}

var errEncodedBodyLimit = errors.New("direct HTTP encoded body limit")

func (b *observedBody) Read(p []byte) (int, error) {
	if b.bytes > greenhouseBodyLimit {
		b.readError = errEncodedBodyLimit
		return 0, errEncodedBodyLimit
	}
	if int64(len(p)) > greenhouseBodyLimit+1-b.bytes {
		p = p[:greenhouseBodyLimit+1-b.bytes]
	}
	n, err := b.ReadCloser.Read(p)
	b.bytes += int64(n)
	b.observation.noteBytes(n)
	if b.bytes > greenhouseBodyLimit {
		err = errEncodedBodyLimit
	}
	if err != nil {
		if err != io.EOF {
			b.readError = err
		}
		b.release()
	}
	return n, err
}

const greenhouseBodyLimit = 64 << 20

func (b *observedBody) Close() error {
	err := b.ReadCloser.Close()
	b.release()
	return err
}
