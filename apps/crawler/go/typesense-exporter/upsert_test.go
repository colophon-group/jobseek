package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestImportDocsAcknowledgements(t *testing.T) {
	docs := []map[string]any{{"id": "one", "title": "First"}, {"id": "two", "title": "Second"}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.String() != "/collections/job_posting/documents/import?action=upsert" || r.Header.Get("X-TYPESENSE-API-KEY") != "test-key" {
			t.Error("request contract mismatch")
		}
		body, _ := io.ReadAll(r.Body)
		if strings.Count(string(body), "\n") != 1 {
			t.Error("expected two JSONL documents")
		}
		w.Write([]byte("{\"success\":true}\n{\"success\":false,\"error\":\"bad row\"}"))
	}))
	defer server.Close()
	failed, err := importDocs(context.Background(), server.Client(), server.URL, "test-key", docs)
	if err != nil {
		t.Fatal(err)
	}
	if len(failed) != 1 {
		t.Fatalf("failed IDs: %v", failed)
	}
	if failure, ok := failed["two"]; !ok || failure.Reason != "bad row" {
		t.Fatalf("wrong failed ID: %v", failed)
	}
}

func TestImportDocsAcceptsOneTerminalNewline(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("{\"success\":true}\n"))
	}))
	defer server.Close()
	failed, err := importDocs(context.Background(), server.Client(), server.URL, "test-key", []map[string]any{{"id": "one"}})
	if err != nil || len(failed) != 0 {
		t.Fatalf("single terminated acknowledgement was rejected: %v, %v", failed, err)
	}
}

func TestImportDocsPinsCursorOnUnprovableResponse(t *testing.T) {
	docs := []map[string]any{{"id": "one"}, {"id": "two"}}
	for _, body := range []string{
		"{\"success\":true}",
		"{\"success\":true}\n{\"ok\":true}",
		"{\"success\":true}\n{\"success\":1}",
		"{\"success\":true}\n{\"success\":null}",
		"{\"success\":true}\n{\"success\":\"false\"}",
		"{\"success\":true}\n{bad json}",
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte(body)) }))
		_, err := importDocs(context.Background(), server.Client(), server.URL, "test-key", docs)
		server.Close()
		if err == nil {
			t.Fatalf("accepted unprovable response %q", body)
		}
	}
}

func TestImportDocsPinsCursorOnHTTPFailure(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) }))
	defer server.Close()
	_, err := importDocs(context.Background(), server.Client(), server.URL, "test-key", []map[string]any{{"id": "one"}})
	if err == nil {
		t.Fatal("accepted unavailable Typesense")
	}
}
