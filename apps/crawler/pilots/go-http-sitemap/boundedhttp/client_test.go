package boundedhttp

import (
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

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
			if stats := session.Stats(); stats.Requests != 1 || stats.DecodedBytes != 0 {
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
