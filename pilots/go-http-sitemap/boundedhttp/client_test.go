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
	"net/url"
	"strings"
	"sync"
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

func waitForCondition(t *testing.T, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatal("condition was not met")
		}
		time.Sleep(time.Millisecond)
	}
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
	for _, shared := range []*SharedTransportConfig{
		{},
		{MaxIdleConns: 2, MaxIdleConnsPerHost: 3, MaxConnsPerHost: 3, MaxConnections: 3, MaxConcurrentRequests: 3, IdleConnTimeout: time.Second},
		{MaxIdleConns: 3, MaxIdleConnsPerHost: 3, MaxConnsPerHost: 2, MaxConnections: 3, MaxConcurrentRequests: 3, IdleConnTimeout: time.Second},
		{MaxIdleConns: 4, MaxIdleConnsPerHost: 2, MaxConnsPerHost: 2, MaxConnections: 3, MaxConcurrentRequests: 3, IdleConnTimeout: time.Second},
	} {
		config := Config{
			RequestTimeout:           time.Second,
			MaxDecodedBodyBytes:      1,
			MaxRequests:              1,
			MaxAggregateDecodedBytes: 1,
			SharedTransport:          shared,
		}
		if _, err := New(config); errorKind(t, err) != ErrorConfig {
			t.Fatal("expected shared-transport config error")
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

func TestSharedTransportReusesConnectionsAcrossSessionsUntilClientClose(t *testing.T) {
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

	shared := &SharedTransportConfig{
		MaxIdleConns:          4,
		MaxIdleConnsPerHost:   2,
		MaxConnsPerHost:       2,
		MaxConnections:        4,
		MaxConcurrentRequests: 2,
		IdleConnTimeout:       time.Minute,
	}
	client := testClient(t, func(config *Config) { config.SharedTransport = shared })
	if client.transport.MaxIdleConns != shared.MaxIdleConns || client.transport.MaxIdleConnsPerHost != shared.MaxIdleConnsPerHost || client.transport.MaxConnsPerHost != shared.MaxConnsPerHost || client.transport.IdleConnTimeout != shared.IdleConnTimeout {
		t.Fatalf("shared transport bounds were not applied: %#v", client.transport)
	}
	if cap(client.requestPermits) != shared.MaxConcurrentRequests {
		t.Fatalf("global request permits=%d", cap(client.requestPermits))
	}
	if cap(client.connections.permits) != shared.MaxConnections {
		t.Fatalf("global connection permits=%d", cap(client.connections.permits))
	}
	for range 2 {
		session := client.NewSession()
		if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
			t.Fatal(err)
		}
		// Session.Close must not close a Client-owned shared pool.
		session.Close()
	}
	if connections.Load() != 1 {
		t.Fatalf("connections before client close=%d", connections.Load())
	}

	client.Close()
	session := client.NewSession()
	defer session.Close()
	if _, err := session.Get(context.Background(), server.URL, nil); err != nil {
		t.Fatal(err)
	}
	if connections.Load() != 2 {
		t.Fatalf("connections after client close=%d", connections.Load())
	}
}

func TestSharedTransportKeepsConcurrentSessionStatsIsolated(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes := int(r.URL.Path[1] - 'a' + 1)
		_, _ = w.Write([]byte(strings.Repeat("x", bodyBytes)))
	}))
	defer server.Close()

	const sessionCount = 20
	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          sessionCount,
			MaxIdleConnsPerHost:   sessionCount,
			MaxConnsPerHost:       sessionCount,
			MaxConnections:        sessionCount,
			MaxConcurrentRequests: sessionCount,
			IdleConnTimeout:       time.Minute,
		}
		config.MaxDecodedBodyBytes = sessionCount
		config.MaxAggregateDecodedBytes = sessionCount
		config.MaxRequests = 1
	})
	defer client.Close()

	sessions := make([]*Session, sessionCount)
	errs := make(chan error, sessionCount)
	var wg sync.WaitGroup
	for index := range sessionCount {
		sessions[index] = client.NewSession()
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			_, err := sessions[index].Get(context.Background(), fmt.Sprintf("%s/%c", server.URL, 'a'+index), nil)
			errs <- err
		}(index)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	for index, session := range sessions {
		stats := session.Stats()
		if stats.Requests != 1 || stats.WireAttempts != 1 || stats.DecodedBytes != int64(index+1) || stats.StatusBodyBytes != 0 {
			t.Fatalf("session %d stats=%+v", index, stats)
		}
		session.Close()
	}
}

func TestSharedTransportCapsActiveRequestsAcrossOrigins(t *testing.T) {
	var active atomic.Int32
	var maxActive atomic.Int32
	started := make(chan struct{}, 6)
	release := make(chan struct{})
	handler := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		current := active.Add(1)
		for observed := maxActive.Load(); current > observed; observed = maxActive.Load() {
			if maxActive.CompareAndSwap(observed, current) {
				break
			}
		}
		w.WriteHeader(http.StatusOK)
		if flusher, ok := w.(http.Flusher); ok {
			flusher.Flush()
		}
		started <- struct{}{}
		<-release
		active.Add(-1)
		_, _ = w.Write([]byte("ok"))
	})
	first := httptest.NewServer(handler)
	defer first.Close()
	second := httptest.NewServer(handler)
	defer second.Close()

	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          8,
			MaxIdleConnsPerHost:   4,
			MaxConnsPerHost:       4,
			MaxConnections:        8,
			MaxConcurrentRequests: 2,
			IdleConnTimeout:       time.Minute,
		}
	})
	defer client.Close()

	errCh := make(chan error, 6)
	for index := range 6 {
		endpoint := first.URL
		if index%2 == 1 {
			endpoint = second.URL
		}
		go func() {
			session := client.NewSession()
			defer session.Close()
			_, err := session.Get(context.Background(), endpoint, nil)
			errCh <- err
		}()
	}
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("two requests did not start")
		}
	}
	time.Sleep(20 * time.Millisecond)
	if got := maxActive.Load(); got != 2 {
		t.Fatalf("max active requests=%d", got)
	}
	if got := len(started); got != 0 {
		t.Fatalf("additional requests crossed the global permit: %d", got)
	}
	close(release)
	for range 6 {
		if err := <-errCh; err != nil {
			t.Fatal(err)
		}
	}
	if got := maxActive.Load(); got != 2 {
		t.Fatalf("final max active requests=%d", got)
	}
}

func TestSharedTransportPermitWaitIsContextAwareAndDoesNotConsumeRequestCap(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		close(started)
		<-release
		_, _ = w.Write([]byte("ok"))
	}))
	defer server.Close()

	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          2,
			MaxIdleConnsPerHost:   2,
			MaxConnsPerHost:       2,
			MaxConnections:        2,
			MaxConcurrentRequests: 1,
			IdleConnTimeout:       time.Minute,
		}
	})
	defer client.Close()

	first := client.NewSession()
	firstDone := make(chan error, 1)
	go func() {
		_, err := first.Get(context.Background(), server.URL, nil)
		firstDone <- err
	}()
	<-started

	canceled := client.NewSession()
	canceledContext, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := canceled.Get(canceledContext, server.URL, nil); errorKind(t, err) != ErrorCanceled {
		t.Fatalf("canceled wait error=%v", err)
	}
	if stats := canceled.Stats(); stats.Requests != 0 || stats.WireAttempts != 0 {
		t.Fatalf("canceled stats=%+v", stats)
	}

	timedOut := client.NewSession()
	deadlineContext, deadlineCancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer deadlineCancel()
	if _, err := timedOut.Get(deadlineContext, server.URL, nil); errorKind(t, err) != ErrorTimeout {
		t.Fatalf("deadline wait error=%v", err)
	}
	if stats := timedOut.Stats(); stats.Requests != 0 || stats.WireAttempts != 0 {
		t.Fatalf("deadline stats=%+v", stats)
	}

	close(release)
	if err := <-firstDone; err != nil {
		t.Fatal(err)
	}
	first.Close()
}

func TestSharedTransportRejectsDoneContextBeforeAvailableRequestPermit(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		_, _ = w.Write([]byte("ok"))
	}))
	defer server.Close()

	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          1,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        1,
			MaxConcurrentRequests: 1,
			IdleConnTimeout:       time.Minute,
		}
	})
	defer client.Close()

	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	expired, expiredCancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer expiredCancel()
	for name, testCase := range map[string]struct {
		ctx  context.Context
		kind ErrorKind
	}{
		"canceled": {ctx: canceled, kind: ErrorCanceled},
		"deadline": {ctx: expired, kind: ErrorTimeout},
	} {
		t.Run(name, func(t *testing.T) {
			session := client.NewSession()
			if _, err := session.Get(testCase.ctx, server.URL, nil); errorKind(t, err) != testCase.kind {
				t.Fatalf("error=%v", err)
			}
			if stats := session.Stats(); stats.Requests != 0 || stats.WireAttempts != 0 {
				t.Fatalf("stats=%+v", stats)
			}
		})
	}
	if requests.Load() != 0 || len(client.requestPermits) != 0 || len(client.connections.permits) != 0 {
		t.Fatalf("requests=%d request_permits=%d connection_permits=%d", requests.Load(), len(client.requestPermits), len(client.connections.permits))
	}
}

func TestSharedTransportGloballyCapsActiveAndIdleConnectionsAcrossOrigins(t *testing.T) {
	servers := make([]*httptest.Server, 4)
	for index := range servers {
		servers[index] = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte("ok"))
		}))
		defer servers[index].Close()
	}

	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          2,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        2,
			MaxConcurrentRequests: 2,
			// Progress below must come from explicit saturated-pool eviction,
			// not the configured idle timeout.
			IdleConnTimeout: time.Hour,
		}
	})
	defer client.Close()

	for index := range 2 {
		session := client.NewSession()
		if _, err := session.Get(context.Background(), servers[index].URL, nil); err != nil {
			t.Fatal(err)
		}
		session.Close()
	}
	if open, permits := client.connections.open.Load(), len(client.connections.permits); open != 2 || permits != 2 {
		t.Fatalf("old-origin idle pool: open=%d permits=%d", open, permits)
	}

	done := make(chan error, 2)
	for index := 2; index < 4; index++ {
		endpoint := servers[index].URL
		go func() {
			session := client.NewSession()
			defer session.Close()
			_, err := session.Get(context.Background(), endpoint, nil)
			done <- err
		}()
	}
	for range 2 {
		select {
		case err := <-done:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(time.Second):
			t.Fatal("new-origin wave did not progress after old idle eviction")
		}
	}
	if maximum := client.connections.maxOpen.Load(); maximum > 2 {
		t.Fatalf("maximum open connections=%d", maximum)
	}
	if permits := len(client.connections.permits); permits > 2 {
		t.Fatalf("connection permits=%d", permits)
	}

	client.Close()
	waitForCondition(t, func() bool {
		return client.connections.open.Load() == 0 && len(client.connections.permits) == 0
	})
}

func TestSharedConnectionWaiterEvictsSocketsThatBecomeIdleLater(t *testing.T) {
	oldStarted := make(chan struct{}, 1)
	releaseOld := make(chan struct{})
	oldOrigin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		oldStarted <- struct{}{}
		<-releaseOld
		_, _ = w.Write([]byte("old"))
	}))
	defer oldOrigin.Close()
	newOrigin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("new"))
	}))
	defer newOrigin.Close()

	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          1,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        1,
			MaxConcurrentRequests: 2,
			IdleConnTimeout:       time.Hour,
		}
	})
	defer client.Close()

	oldDone := make(chan error, 1)
	go func() {
		_, err := client.NewSession().Get(context.Background(), oldOrigin.URL, nil)
		oldDone <- err
	}()
	<-oldStarted
	newDone := make(chan error, 1)
	go func() {
		_, err := client.NewSession().Get(context.Background(), newOrigin.URL, nil)
		newDone <- err
	}()
	waitForCondition(t, func() bool { return client.connections.waiters.Load() == 1 })
	close(releaseOld)
	if err := <-oldDone; err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-newDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("connection waiter did not progress when active socket became idle")
	}
	if maximum := client.connections.maxOpen.Load(); maximum > 1 {
		t.Fatalf("maximum open connections=%d", maximum)
	}
}

func TestSharedConnectionPermitIsReleasedOnDialAndTLSFailure(t *testing.T) {
	client := testClient(t, func(config *Config) {
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          1,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        1,
			MaxConcurrentRequests: 1,
			IdleConnTimeout:       time.Minute,
		}
	})
	defer client.Close()

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	unreachableURL := "http://" + listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := client.NewSession().Get(context.Background(), unreachableURL, nil); errorKind(t, err) != ErrorTransport {
		t.Fatalf("dial error=%v", err)
	}
	waitForCondition(t, func() bool { return len(client.connections.permits) == 0 })

	tlsServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("unexpected"))
	}))
	defer tlsServer.Close()
	if _, err := client.NewSession().Get(context.Background(), tlsServer.URL, nil); errorKind(t, err) != ErrorTransport {
		t.Fatalf("TLS error=%v", err)
	}
	waitForCondition(t, func() bool {
		return client.connections.open.Load() == 0 && len(client.connections.permits) == 0
	})
}

func TestConnectionLimiterReleasesPermitWhenDialIsCanceled(t *testing.T) {
	limiter := &connectionLimiter{
		permits:   make(chan struct{}, 1),
		closeIdle: func() {},
	}
	dialStarted := make(chan struct{})
	dial := limiter.wrapDialContext(func(ctx context.Context, _, _ string) (net.Conn, error) {
		close(dialStarted)
		<-ctx.Done()
		return nil, ctx.Err()
	})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := dial(ctx, "tcp", "example.test:80")
		done <- err
	}()
	<-dialStarted
	if len(limiter.permits) != 1 {
		t.Fatalf("permits during dial=%d", len(limiter.permits))
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("dial error=%v", err)
	}
	if len(limiter.permits) != 0 || limiter.open.Load() != 0 {
		t.Fatalf("permits=%d open=%d", len(limiter.permits), limiter.open.Load())
	}
}

func TestConnectionLimiterReleasesSuccessfulConnectionExactlyOnce(t *testing.T) {
	limiter := &connectionLimiter{
		permits:   make(chan struct{}, 1),
		closeIdle: func() {},
	}
	clientConn, serverConn := net.Pipe()
	defer serverConn.Close()
	dial := limiter.wrapDialContext(func(context.Context, string, string) (net.Conn, error) {
		return clientConn, nil
	})
	conn, err := dial(context.Background(), "tcp", "example.test:80")
	if err != nil {
		t.Fatal(err)
	}
	if len(limiter.permits) != 1 || limiter.open.Load() != 1 || limiter.maxOpen.Load() != 1 {
		t.Fatalf("after dial: permits=%d open=%d max=%d", len(limiter.permits), limiter.open.Load(), limiter.maxOpen.Load())
	}
	if err := conn.Close(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	if len(limiter.permits) != 0 || limiter.open.Load() != 0 {
		t.Fatalf("after repeated close: permits=%d open=%d", len(limiter.permits), limiter.open.Load())
	}
}

func TestSharedHostAdmissionPreventsSameOriginTransportHandoffStarvation(t *testing.T) {
	firstAStarted := make(chan struct{})
	releaseFirstA := make(chan struct{})
	requestOrder := make(chan string, 3)
	var aRequests atomic.Int32
	aOrigin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if aRequests.Add(1) == 1 {
			close(firstAStarted)
			<-releaseFirstA
		} else {
			requestOrder <- "A"
		}
		_, _ = w.Write([]byte("a"))
	}))
	defer aOrigin.Close()
	bOrigin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requestOrder <- "B"
		_, _ = w.Write([]byte("b"))
	}))
	defer bOrigin.Close()

	client := testClient(t, func(config *Config) {
		config.MaxRequests = 1
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          1,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        1,
			MaxConcurrentRequests: 3,
			IdleConnTimeout:       time.Hour,
		}
	})
	defer client.Close()

	type outcome struct {
		name string
		err  error
	}
	outcomes := make(chan outcome, 4)
	run := func(name, endpoint string) {
		go func() {
			session := client.NewSession()
			_, err := session.Get(context.Background(), endpoint, nil)
			outcomes <- outcome{name: name, err: err}
		}()
	}

	run("A1", aOrigin.URL)
	<-firstAStarted
	run("A2", aOrigin.URL)
	waitForCondition(t, func() bool { return client.hosts.refs(aOrigin.URL) == 2 })
	run("B", bOrigin.URL)
	waitForCondition(t, func() bool { return client.connections.waiters.Load() == 1 })
	run("A3", aOrigin.URL)
	waitForCondition(t, func() bool { return client.hosts.refs(aOrigin.URL) == 3 })

	close(releaseFirstA)
	select {
	case origin := <-requestOrder:
		if origin != "B" {
			t.Fatalf("same-origin backlog recycled the connection before B: first=%s", origin)
		}
	case <-time.After(time.Second):
		t.Fatal("neither queued origin progressed")
	}

	seen := make(map[string]bool)
	for range 4 {
		select {
		case outcome := <-outcomes:
			if outcome.err != nil {
				t.Fatalf("%s: %v", outcome.name, outcome.err)
			}
			seen[outcome.name] = true
		case <-time.After(time.Second):
			t.Fatal("timed out waiting for admitted requests")
		}
	}
	if len(seen) != 4 || client.connections.maxOpen.Load() > 1 {
		t.Fatalf("outcomes=%v max_open=%d", seen, client.connections.maxOpen.Load())
	}
	waitForCondition(t, func() bool {
		return client.hosts.size() == 0 && len(client.requestPermits) == 0
	})
}

func TestSharedHostAdmissionCancellationDoesNotConsumeGlobalAdmission(t *testing.T) {
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		close(firstStarted)
		<-releaseFirst
		_, _ = w.Write([]byte("ok"))
	}))
	defer server.Close()

	client := testClient(t, func(config *Config) {
		config.MaxRequests = 1
		config.SharedTransport = &SharedTransportConfig{
			MaxIdleConns:          1,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       1,
			MaxConnections:        1,
			MaxConcurrentRequests: 2,
			IdleConnTimeout:       time.Hour,
		}
	})
	defer client.Close()

	first := client.NewSession()
	firstDone := make(chan error, 1)
	go func() {
		_, err := first.Get(context.Background(), server.URL, nil)
		firstDone <- err
	}()
	<-firstStarted

	waiting := client.NewSession()
	waitingContext, cancelWaiting := context.WithCancel(context.Background())
	waitingDone := make(chan error, 1)
	go func() {
		_, err := waiting.Get(waitingContext, server.URL, nil)
		waitingDone <- err
	}()
	waitForCondition(t, func() bool { return client.hosts.refs(server.URL) == 2 })
	cancelWaiting()
	if err := <-waitingDone; errorKind(t, err) != ErrorCanceled {
		t.Fatalf("waiting error=%v", err)
	}
	if stats := waiting.Stats(); stats.Requests != 0 || stats.WireAttempts != 0 {
		t.Fatalf("waiting stats=%+v", stats)
	}
	if refs, global := client.hosts.refs(server.URL), len(client.requestPermits); refs != 1 || global != 1 {
		t.Fatalf("host refs=%d global permits=%d", refs, global)
	}

	close(releaseFirst)
	if err := <-firstDone; err != nil {
		t.Fatal(err)
	}
	waitForCondition(t, func() bool {
		return client.hosts.size() == 0 && len(client.requestPermits) == 0
	})
}

func TestCanonicalOriginNormalizesCaseAndDefaultPorts(t *testing.T) {
	for rawURL, expected := range map[string]string{
		"http://EXAMPLE.com:80/a":   "http://example.com",
		"https://EXAMPLE.com:443/a": "https://example.com",
		"https://[::1]:8443/a":      "https://[::1]:8443",
	} {
		parsed, err := url.Parse(rawURL)
		if err != nil {
			t.Fatal(err)
		}
		if origin := canonicalOrigin(parsed); origin != expected {
			t.Fatalf("canonicalOrigin(%q)=%q, want %q", rawURL, origin, expected)
		}
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
