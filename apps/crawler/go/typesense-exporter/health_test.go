package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestProbeTypesenseRequiresHealthyResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" || r.Header.Get("X-TYPESENSE-API-KEY") != "test-key" {
			t.Error("Typesense health request contract mismatch")
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()
	healthy, err := probeTypesense(context.Background(), server.Client(), server.URL, "test-key")
	if err != nil || !healthy {
		t.Fatalf("expected healthy response: healthy=%v error=%v", healthy, err)
	}
}
