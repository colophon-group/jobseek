package boundedhttp

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestResponseAccountingIncludesStatusResponses(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/missing" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer server.Close()
	client, err := New(Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      1024,
		MaxRequests:              2,
		MaxAggregateDecodedBytes: 2048,
		AllowPrivateNetwork:      true,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	session := client.NewSession()
	defer session.Close()
	for _, path := range []string{"/ok", "/missing"} {
		if _, err := session.Get(context.Background(), server.URL+path, nil); err != nil {
			t.Fatal(err)
		}
	}
	stats := session.Stats()
	if stats.Requests != 2 || stats.Responses != 2 || stats.WireAttempts != 2 {
		t.Fatalf("unexpected accounting: %+v", stats)
	}
}
