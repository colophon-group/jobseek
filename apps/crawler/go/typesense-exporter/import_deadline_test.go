package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestExporterImportAcceptsAcknowledgementsAfterProbeSizedDeadline(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
			return
		case <-time.After(11 * time.Second):
		}
		_, _ = w.Write([]byte("{\"success\":true}\n{\"success\":false,\"error\":\"invalid row\"}\n"))
	}))
	defer server.Close()
	client := newExporterHTTPClient()
	defer client.CloseIdleConnections()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	failed, err := importDocs(ctx, client, server.URL, "synthetic-key", []map[string]any{{"id": "one"}, {"id": "two"}})
	if err != nil || len(failed) != 1 || failed["two"].Reason != "invalid row" {
		t.Fatal("slow complete acknowledgement did not preserve row outcomes", failed, err)
	}
}

func TestExporterImportBudgetStillHonorsCancellationAndShortHealthProbe(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		<-r.Context().Done()
	}))
	defer server.Close()
	client := newExporterHTTPClient()
	defer client.CloseIdleConnections()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	failed, err := importDocs(ctx, client, server.URL, "synthetic-key", []map[string]any{{"id": "one"}})
	if !errors.Is(err, context.DeadlineExceeded) || failed != nil {
		t.Fatal("cancelled import returned a handled batch", failed, err)
	}
	started := time.Now()
	if healthy, err := probeTypesense(context.Background(), client, server.URL, "synthetic-key"); healthy || err == nil || time.Since(started) > 5*time.Second {
		t.Fatal("bulk budget escaped the independent health deadline", healthy, err)
	}
}
