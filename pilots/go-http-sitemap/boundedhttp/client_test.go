package boundedhttp

import (
	"compress/gzip"
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type testConnectionIDKey struct{}

func testClient(t *testing.T, mutate func(*Config)) *Client {
	t.Helper()
	config := Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      64,
		MaxRequests:              3,
		MaxAggregateDecodedBytes: 128,
	}
	if mutate != nil {
		mutate(&config)
	}
	client, err := New(config)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func errorKind(t *testing.T, err error) ErrorKind {
	t.Helper()
	var boundedErr *Error
	if !errors.As(err, &boundedErr) {
		t.Fatalf("expected bounded error, got %T: %v", err, err)
	}
	return boundedErr.Kind
}

func TestNewRejectsUnboundedConfig(t *testing.T) {
	if _, err := New(Config{}); errorKind(t, err) != ErrorConfig {
		t.Fatal("expected config error")
	}
	for _, mutate := range []func(*Config){
		func(c *Config) { c.MaxDecodedBodyBytes = math.MaxInt64 },
		func(c *Config) { c.MaxAggregateDecodedBytes = math.MaxInt64 },
	} {
		config := Config{RequestTimeout: time.Second, MaxDecodedBodyBytes: 1, MaxRequests: 1, MaxAggregateDecodedBytes: 1}
		mutate(&config)
		if _, err := New(config); errorKind(t, err) != ErrorConfig {
			t.Fatal("expected sentinel-overflow config error")
		}
	}
}

func TestGetCapsDecodedGzipBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Encoding", "gzip")
		zw := gzip.NewWriter(w)
		_, _ = zw.Write([]byte("decoded body is larger than ten bytes"))
		_ = zw.Close()
	}))
	defer server.Close()

	client := testClient(t, func(c *Config) { c.MaxDecodedBodyBytes = 10 })
	session := client.NewSession()
	_, err := session.Get(context.Background(), server.URL, nil)
	if got := errorKind(t, err); got != ErrorBodyLimit {
		t.Fatalf("kind=%s", got)
	}
	if stats := session.Stats(); stats.Requests != 1 || stats.DecodedBytes != 11 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestGetEnforcesRequestAndAggregateLimits(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("four"))
	}))
	defer server.Close()

	t.Run("requests", func(t *testing.T) {
		client := testClient(t, func(c *Config) { c.MaxRequests = 1 })
		session := client.NewSession()
		if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
			t.Fatal(err)
		}
		_, err := session.Get(context.Background(), server.URL, nil)
		if got := errorKind(t, err); got != ErrorRequestLimit {
			t.Fatalf("kind=%s", got)
		}
	})

	t.Run("aggregate decoded bytes", func(t *testing.T) {
		client := testClient(t, func(c *Config) { c.MaxAggregateDecodedBytes = 6 })
		session := client.NewSession()
		if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
			t.Fatal(err)
		}
		_, err := session.Get(context.Background(), server.URL, nil)
		if got := errorKind(t, err); got != ErrorAggregateLimit {
			t.Fatalf("kind=%s", got)
		}
		if stats := session.Stats(); stats.Requests != 2 || stats.DecodedBytes != 7 {
			t.Fatalf("stats=%+v", stats)
		}
	})
}

func TestGetCancellationInterruptsBodyRead(t *testing.T) {
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		close(started)
		<-r.Context().Done()
	}))
	defer server.Close()

	client := testClient(t, nil)
	session := client.NewSession()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := session.Get(ctx, server.URL, nil)
		done <- err
	}()
	<-started
	cancel()
	if got := errorKind(t, <-done); got != ErrorCanceled {
		t.Fatalf("kind=%s", got)
	}
	if stats := session.Stats(); stats.Requests != 1 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestGetCancellationInterruptsStatusBodyDrain(t *testing.T) {
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		close(started)
		<-r.Context().Done()
	}))
	defer server.Close()

	session := testClient(t, nil).NewSession()
	defer session.Close()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := session.Get(ctx, server.URL, nil)
		done <- err
	}()
	<-started
	cancel()
	if got := errorKind(t, <-done); got != ErrorCanceled {
		t.Fatalf("kind=%s", got)
	}
	if stats := session.Stats(); stats.Requests != 1 || stats.WireAttempts != 1 {
		t.Fatalf("stats=%+v", stats)
	}
}

func TestGetClassifiesRequestTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		<-r.Context().Done()
	}))
	defer server.Close()

	client := testClient(t, func(c *Config) { c.RequestTimeout = 20 * time.Millisecond })
	_, err := client.NewSession().Get(context.Background(), server.URL, nil)
	if got := errorKind(t, err); got != ErrorTimeout {
		t.Fatalf("kind=%s", got)
	}
}

func TestGetDoesNotFollowRedirectOutsidePilotCohort(t *testing.T) {
	var redirectedRequests int
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		redirectedRequests++
	}))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Location", target.URL)
		w.WriteHeader(http.StatusFound)
	}))
	defer server.Close()

	session := testClient(t, nil).NewSession()
	response, err := session.Get(context.Background(), server.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusFound || redirectedRequests != 0 || session.Stats().Requests != 1 {
		t.Fatalf("status=%d redirects=%d stats=%+v", response.StatusCode, redirectedRequests, session.Stats())
	}
}

func TestGetReturnsNonSuccessStatusWithoutReadingLargeBody(t *testing.T) {
	for _, status := range []int{http.StatusNotFound, http.StatusGone, http.StatusInternalServerError} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(status)
				_, _ = w.Write([]byte(strings.Repeat("x", 256)))
			}))
			defer server.Close()

			session := testClient(t, func(c *Config) {
				c.MaxDecodedBodyBytes = 8
				c.MaxAggregateDecodedBytes = 8
			}).NewSession()
			response, err := session.Get(context.Background(), server.URL, nil)
			if err != nil {
				t.Fatal(err)
			}
			if response.StatusCode != status || len(response.Body) != 0 {
				t.Fatalf("response=%+v", response)
			}
			if stats := session.Stats(); stats.Requests != 1 || stats.WireAttempts != 1 || stats.DecodedBytes != 0 || stats.StatusBodyBytes != 8 {
				t.Fatalf("stats=%+v", stats)
			}
		})
	}
}

func TestErrorsRedactURLSecrets(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok"))
	}))
	secretURL := server.URL + "/sitemap.xml?token=secret#fragment"
	server.Close()

	_, err := testClient(t, nil).NewSession().Get(context.Background(), secretURL, nil)
	var boundedErr *Error
	if !errors.As(err, &boundedErr) {
		t.Fatalf("error=%v", err)
	}
	if strings.Contains(fmt.Sprint(err), "secret") || strings.Contains(fmt.Sprintf("%+v", boundedErr), "secret") || boundedErr.URL != server.URL+"/sitemap.xml" {
		t.Fatalf("secret-bearing error=%+v", boundedErr)
	}

	_, err = testClient(t, nil).NewSession().Get(context.Background(), "https://user:password@example.com/sitemap.xml?token=secret#fragment", nil)
	if !errors.As(err, &boundedErr) || boundedErr.URL != "https://example.com/sitemap.xml" || strings.Contains(fmt.Sprintf("%+v", boundedErr), "password") {
		t.Fatalf("userinfo-bearing error=%+v", boundedErr)
	}
}

func TestRequestCapMatchesWireRequestsWithoutTransparentRetries(t *testing.T) {
	var connections atomic.Int32
	var wireRequests atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		wireRequests.Add(1)
		if r.ProtoMajor != 1 {
			t.Errorf("protocol=%s", r.Proto)
		}
		if r.ContentLength != 0 || len(r.TransferEncoding) != 0 {
			t.Errorf("GET was not bodyless: content_length=%d transfer_encoding=%v", r.ContentLength, r.TransferEncoding)
		}
		_, _ = w.Write([]byte("ok"))
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.Start()
	defer server.Close()

	client := testClient(t, func(config *Config) { config.MaxRequests = 2 })
	transport := client.transport
	if transport.DisableKeepAlives || transport.ForceAttemptHTTP2 || transport.TLSNextProto == nil || len(transport.TLSNextProto) != 0 || transport.TLSClientConfig == nil || len(transport.TLSClientConfig.NextProtos) != 1 || transport.TLSClientConfig.NextProtos[0] != "http/1.1" {
		t.Fatalf("pilot transport is not pooled HTTP/1: %#v", transport)
	}
	session := client.NewSession()
	defer session.Close()
	for range 2 {
		if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := session.Get(context.Background(), server.URL, nil); errorKind(t, err) != ErrorRequestLimit {
		t.Fatal("expected request limit")
	}
	if stats := session.Stats(); stats.Requests != 2 || stats.WireAttempts != 2 || wireRequests.Load() != 2 || connections.Load() != 1 {
		t.Fatalf("stats=%+v wire=%d connections=%d", stats, wireRequests.Load(), connections.Load())
	}
}

func TestConnectionPoolIsScopedToSession(t *testing.T) {
	var connections atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok"))
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.Start()
	defer server.Close()

	client := testClient(t, nil)
	for range 2 {
		session := client.NewSession()
		if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
			t.Fatal(err)
		}
		session.Close()
	}
	if connections.Load() != 2 {
		t.Fatalf("connections=%d", connections.Load())
	}
}

func TestStalePooledConnectionIsNotTransparentlyReplayed(t *testing.T) {
	var connections atomic.Int32
	var requests atomic.Int32
	var requestsOnFirstConnection atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		connectionID, ok := r.Context().Value(testConnectionIDKey{}).(int32)
		if !ok {
			t.Error("missing connection identity")
			return
		}
		if connectionID == 1 && requestsOnFirstConnection.Add(1) == 2 {
			// The second explicit GET is fully written on a reused connection,
			// then loses that connection before a response. A replayable GET
			// would make net/http silently issue the same request on connection 2.
			hijacker, ok := w.(http.Hijacker)
			if !ok {
				t.Error("HTTP/1 response writer cannot hijack")
				return
			}
			conn, _, err := hijacker.Hijack()
			if err != nil {
				t.Errorf("hijack: %v", err)
				return
			}
			_ = conn.Close()
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	server.Config.ConnContext = func(ctx context.Context, _ net.Conn) context.Context {
		return context.WithValue(ctx, testConnectionIDKey{}, connections.Add(1))
	}
	server.Start()
	defer server.Close()

	session := testClient(t, func(config *Config) { config.MaxRequests = 3 }).NewSession()
	defer session.Close()
	if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := session.Get(context.Background(), server.URL, nil); errorKind(t, err) != ErrorTransport {
		t.Fatalf("expected the explicit attempt to fail without transparent replay, got %v", err)
	}
	if stats := session.Stats(); stats.Requests != 2 || stats.WireAttempts != 2 || requests.Load() != 2 || connections.Load() != 1 {
		t.Fatalf("after failed attempt: stats=%+v requests=%d connections=%d", stats, requests.Load(), connections.Load())
	}
	if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
		t.Fatal(err)
	}
	if stats := session.Stats(); stats.Requests != 3 || stats.WireAttempts != 3 || requests.Load() != 3 || connections.Load() != 2 {
		t.Fatalf("after explicit recovery: stats=%+v requests=%d connections=%d", stats, requests.Load(), connections.Load())
	}
	if _, err := session.Get(context.Background(), server.URL, nil); errorKind(t, err) != ErrorRequestLimit {
		t.Fatal("expected request limit")
	}
	if stats := session.Stats(); stats.Requests != 3 || stats.WireAttempts != 3 || requests.Load() != 3 {
		t.Fatalf("request cap changed counters: stats=%+v requests=%d", stats, requests.Load())
	}
}

func TestForceHTTP1ALPNClonesExistingTLSConfig(t *testing.T) {
	original := &tls.Config{
		MinVersion: tls.VersionTLS12,
		NextProtos: []string{"h2"},
		ServerName: "example.test",
	}
	transport := &http.Transport{TLSClientConfig: original}

	forceHTTP1ALPN(transport)

	if transport.TLSClientConfig == original {
		t.Fatal("TLS config was mutated instead of cloned")
	}
	if transport.TLSClientConfig.MinVersion != original.MinVersion || transport.TLSClientConfig.ServerName != original.ServerName {
		t.Fatalf("TLS settings were not preserved: %#v", transport.TLSClientConfig)
	}
	if len(transport.TLSClientConfig.NextProtos) != 1 || transport.TLSClientConfig.NextProtos[0] != "http/1.1" {
		t.Fatalf("ALPN protocols=%v", transport.TLSClientConfig.NextProtos)
	}
	if len(original.NextProtos) != 1 || original.NextProtos[0] != "h2" {
		t.Fatalf("original TLS config was modified: %v", original.NextProtos)
	}
}

func TestTLSNegotiatesHTTP11WhenServerOffersHTTP2(t *testing.T) {
	type observation struct {
		requestProtocol    string
		negotiatedProtocol string
	}
	observed := make(chan observation, 1)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observed <- observation{
			requestProtocol:    r.Proto,
			negotiatedProtocol: r.TLS.NegotiatedProtocol,
		}
		_, _ = w.Write([]byte("ok"))
	}))
	server.EnableHTTP2 = true
	server.TLS = &tls.Config{NextProtos: []string{"h2", "http/1.1"}}
	server.StartTLS()
	defer server.Close()

	client := testClient(t, nil)
	transport := client.transport
	tlsConfig := transport.TLSClientConfig.Clone()
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	tlsConfig.RootCAs = roots
	transport.TLSClientConfig = tlsConfig

	session := client.NewSession()
	defer session.Close()
	response, err := session.Get(context.Background(), server.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || string(response.Body) != "ok" {
		t.Fatalf("response=%+v", response)
	}
	got := <-observed
	if got.requestProtocol != "HTTP/1.1" || got.negotiatedProtocol != "http/1.1" {
		t.Fatalf("request protocol=%q negotiated ALPN=%q", got.requestProtocol, got.negotiatedProtocol)
	}
	if stats := session.Stats(); stats.Requests != 1 || stats.WireAttempts != 1 || stats.DecodedBytes != 2 {
		t.Fatalf("stats=%+v", stats)
	}
}
