package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testR2(t *testing.T, handler http.HandlerFunc) (*r2Client, *httptest.Server) {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	client, err := newR2Client(server.URL, "fixture-bucket", "test-key", "test-secret")
	if err != nil {
		server.Close()
		t.Fatal(err)
	}
	client.http = server.Client()
	client.http.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return client, server
}

func TestPutPreservesObjectBytesHeadersAndKey(t *testing.T) {
	var calls atomic.Int32
	client, server := testR2(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodPut || r.URL.EscapedPath() != "/fixture-bucket/job/123/de%20CH/latest.html" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.EscapedPath())
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != "Grüezi <strong>Welt</strong>" {
			t.Errorf("body = %q", body)
		}
		if r.Header.Get("Content-Type") != "text/html" || r.Header.Get("Cache-Control") != "public, max-age=86400" {
			t.Errorf("incorrect object headers: %v", r.Header)
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 ") || r.Header.Get("X-Amz-Date") == "" {
			t.Error("request was not SigV4 signed")
		}
		if r.Header.Get("X-Amz-Content-Sha256") == "" {
			t.Error("signed payload hash header is missing")
		}
		w.WriteHeader(http.StatusOK)
	})
	defer server.Close()
	if err := client.put(context.Background(), description{PostingID: "123", Locale: "de CH", HTML: "Grüezi <strong>Welt</strong>"}); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 {
		t.Fatalf("calls = %d, want 1", calls.Load())
	}
}

func TestPutRetriesOnlyRetryableResponses(t *testing.T) {
	var calls atomic.Int32
	client, server := testR2(t, func(w http.ResponseWriter, _ *http.Request) {
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	defer server.Close()
	if err := client.put(context.Background(), description{PostingID: "123", Locale: "en", HTML: "x"}); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatalf("calls = %d, want 2", calls.Load())
	}
}

func TestPutDoesNotFollowRedirect(t *testing.T) {
	var calls atomic.Int32
	client, server := testR2(t, func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.Header().Set("Location", "https://example.com/elsewhere")
		w.WriteHeader(http.StatusFound)
	})
	defer server.Close()
	err := client.put(context.Background(), description{PostingID: "123", Locale: "en", HTML: "x"})
	if err == nil || calls.Load() != 1 {
		t.Fatalf("redirect result: calls=%d error=%v", calls.Load(), err)
	}
}

func TestRetryDelayStaysWithinEqualJitterBounds(t *testing.T) {
	for _, tc := range []struct {
		failures int32
		ceiling  time.Duration
	}{{1, 5 * time.Second}, {2, 10 * time.Second}, {10, 900 * time.Second}, {1000, 900 * time.Second}} {
		for i := 0; i < 100; i++ {
			d := retryDelay(tc.failures, 5*time.Second, 900*time.Second)
			if d < tc.ceiling/2 || d > tc.ceiling {
				t.Fatalf("failure %d delay %s outside [%s, %s]", tc.failures, d, tc.ceiling/2, tc.ceiling)
			}
		}
	}
}
