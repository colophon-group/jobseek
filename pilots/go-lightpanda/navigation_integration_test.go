package main

import (
	"context"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

// The existing native integration image runs this against the exact pinned
// Lightpanda binary on both architectures. Only the disposable fixture origin
// is admitted; no publisher is contacted.
func TestLightpandaNavigationReadinessIntegration(t *testing.T) {
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("pinned Lightpanda integration requires Linux")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expected); err != nil {
		t.Fatal(err)
	}
	var documentRequests atomic.Int64
	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/held" {
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(200)
			w.(http.Flusher).Flush()
			select {
			case <-r.Context().Done():
			case <-time.After(10 * time.Second):
			}
			return
		}
		if r.URL.Path != "/document" {
			http.NotFound(w, r)
			return
		}
		documentRequests.Add(1)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(201)
		if r.URL.Query().Get("hold") == "1" {
			_, _ = io.WriteString(w, `<!doctype html><html><body><h1 id="ready">Engineer</h1><script>fetch('/held');setTimeout(function(){document.getElementById('ready').textContent='Rendered Engineer'},50)</script></body></html>`)
		} else {
			_, _ = io.WriteString(w, `<!doctype html><html><body><h1 id="ready">Engineer</h1></body></html>`)
		}
	}))
	adapter, err := lightpandaadapter.NewNavigationRenderOnly(runtimeV1Runner{config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, run: testOnlyFixtureRunner(binary, "127.0.0.2")})
	if err != nil {
		t.Fatal(err)
	}
	for _, wait := range []runtimev1.WaitCondition{1, 2, 3, 4} {
		input := bridgeInput(origin.URL+"/document", "", 1024)
		input.Plan.Navigation.WaitUntil = wait
		input.Plan.Navigation.TimeoutMs = 10000
		before := documentRequests.Load()
		result := adapter.Execute(context.Background(), input)
		if success := result.GetSuccess(); success == nil || success.GetStatus() != 201 || !strings.Contains(string(bridgeManifestBody(success.Html)), "Engineer") {
			t.Fatalf("wait %v failed: %v", wait, result)
		}
		if documentRequests.Load() != before+1 {
			t.Fatal("navigation was repeated")
		}
	}
	input := bridgeInput(origin.URL+"/document?hold=1", "", 1024)
	input.Plan.Navigation.WaitUntil = 4
	input.Plan.Navigation.TimeoutMs = 1500
	input.Plan.Navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 1000}
	before := documentRequests.Load()
	result := adapter.Execute(context.Background(), input)
	if success := result.GetSuccess(); success == nil || success.GetStatus() != 201 || !strings.Contains(string(bridgeManifestBody(success.Html)), "Rendered Engineer") {
		t.Fatalf("current-document fallback failed: %v", result)
	}
	if documentRequests.Load() != before+1 {
		t.Fatal("fallback repeated origin navigation")
	}
}
