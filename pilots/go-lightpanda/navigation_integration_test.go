package main

import (
	"bytes"
	"context"
	"io"
	"net"
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
	const feedXML = `<?xml version="1.0"?><rss><channel><item><title>Engineer</title><description><![CDATA[<p>Build & maintain <strong>systems</strong>.</p>]]></description></item></channel></rss>`
	const originPolicyURL = "https://fixture.invalid/license"
	var documentRequests atomic.Int64
	var resetRequests atomic.Int64
	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/feed-redirect" {
			w.Header().Set("TDM-Reservation", "0")
			http.Redirect(w, r, "/feed", http.StatusFound)
			return
		}
		if r.URL.Path == "/feed" {
			w.Header().Set("Content-Type", "application/rss+xml; charset=utf-8")
			w.Header().Set("TDM-Reservation", "1")
			w.Header().Set("TDM-Policy", originPolicyURL)
			w.WriteHeader(200)
			_, _ = io.WriteString(w, feedXML)
			return
		}
		if r.URL.Path == "/reset" {
			attempt := resetRequests.Add(1)
			if attempt == 1 || r.URL.Query().Get("always") == "1" {
				connection, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				if tcp, ok := connection.(*net.TCPConn); ok {
					_ = tcp.SetLinger(0)
				}
				_ = connection.Close()
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.WriteHeader(201)
			_, _ = io.WriteString(w, "<html><body><h1>Recovered Engineer</h1></body></html>")
			return
		}
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
		if r.URL.Path == "/redirect" {
			w.Header().Set("TDM-Reservation", "0")
			http.Redirect(w, r, "/document", http.StatusFound)
			return
		}
		if r.URL.Path != "/document" {
			http.NotFound(w, r)
			return
		}
		documentRequests.Add(1)
		w.Header().Set("TDM-Reservation", "1")
		w.Header().Set("TDM-Policy", originPolicyURL)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(201)
		if r.URL.Query().Get("hold") == "1" {
			_, _ = io.WriteString(w, `<!doctype html><html><body><h1 id="ready">Engineer</h1><script>fetch('/held');setTimeout(function(){document.getElementById('ready').textContent='Rendered Engineer'},50)</script></body></html>`)
		} else {
			_, _ = io.WriteString(w, `<!doctype html><html><body><h1 id="ready">Engineer</h1></body></html>`)
		}
	}))
	fixtureRunner := testOnlyFixtureRunner(binary, "127.0.0.2")
	adapter, err := lightpandaadapter.NewNavigationRenderOnly(runtimeV1Runner{config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, run: func(ctx context.Context, config Config, task Task) (Result, error) {
		result, err := fixtureRunner(ctx, config, task)
		if err != nil {
			t.Logf("fixture navigation failed after cleanup: %T %v", err, err)
		}
		return result, err
	}})
	if err != nil {
		t.Fatal(err)
	}

	t.Run("raw-XML-response-CDATAs-and-final-policy", func(t *testing.T) {
		for _, path := range []string{"/feed", "/feed-redirect"} {
			input := bridgeInput(origin.URL+path, "", 0)
			input.Plan.Navigation.TimeoutMs = 10000
			input.Plan.Navigation.WaitUntil = runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED
			input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE)
			input.Plan.Captures = []*runtimev1.CapturePlan{{CaptureId: "feed", Kind: runtimev1.CaptureKind_CAPTURE_KIND_RESPONSE_BODY, MaxBytes: 2_000_000}}
			result := adapter.Execute(context.Background(), input)
			success := result.GetSuccess()
			if success == nil || success.FinalUrl != origin.URL+"/feed" || len(success.Captures) != 1 || !bytes.Equal(bridgeManifestBody(success.Captures[0].Body), []byte(feedXML)) || success.Html.TotalSizeBytes != 0 || success.ResourcePolicy.GetTdmReservationHeader() != "1" || success.ResourcePolicy.GetTdmPolicyHeader() != originPolicyURL {
				t.Fatalf("raw feed capture differs for %s: %v", path, result)
			}
			input.Plan.Captures[0].MaxBytes = 1
			result = adapter.Execute(context.Background(), input)
			if result.GetError() == nil || result.GetError().Error.Code != runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT {
				t.Fatal("capture ceiling did not refuse", result)
			}
		}
	})
	for _, wait := range []runtimev1.WaitCondition{1, 2, 3, 4} {
		input := bridgeInput(origin.URL+"/document", "", 1024)
		input.Plan.Navigation.WaitUntil = wait
		input.Plan.Navigation.TimeoutMs = 10000
		bindTransportRetry(input)
		before := documentRequests.Load()
		result := adapter.Execute(context.Background(), input)
		if success := result.GetSuccess(); success == nil || success.GetStatus() != 201 || !strings.Contains(string(bridgeManifestBody(success.Html)), "Engineer") {
			t.Fatalf("wait %v failed: %v", wait, result)
		}
		if success := result.GetSuccess(); success.ResourcePolicy == nil || success.ResourcePolicy.GetTdmReservationHeader() != "1" || success.ResourcePolicy.GetTdmPolicyHeader() != originPolicyURL {
			t.Fatal("correlated main-document policy signals missing")
		}
		if documentRequests.Load() != before+1 {
			t.Fatal("navigation was repeated")
		}
	}
	redirectInput := bridgeInput(origin.URL+"/redirect", "", 1024)
	bindTransportRetry(redirectInput)
	redirected := adapter.Execute(context.Background(), redirectInput)
	if success := redirected.GetSuccess(); success == nil || success.FinalUrl != origin.URL+"/document" || success.ResourcePolicy.GetTdmReservationHeader() != "1" {
		t.Fatal("redirect mixed policy signals from another document")
	}
	input := bridgeInput(origin.URL+"/document?hold=1", "", 1024)
	input.Plan.Navigation.WaitUntil = 4
	input.Plan.Navigation.TimeoutMs = 1500
	input.Plan.Navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 1000}
	bindTransportRetry(input)
	before := documentRequests.Load()
	result := adapter.Execute(context.Background(), input)
	if success := result.GetSuccess(); success == nil || success.GetStatus() != 201 || !strings.Contains(string(bridgeManifestBody(success.Html)), "Rendered Engineer") {
		t.Fatalf("current-document fallback failed: %v", result)
	}
	if documentRequests.Load() != before+1 {
		t.Fatal("fallback repeated origin navigation")
	}
	for _, always := range []bool{false, true} {
		resetRequests.Store(0)
		url := origin.URL + "/reset"
		if always {
			url += "?always=1"
		}
		input := bridgeInput(url, "", 1024)
		input.Plan.Navigation.TimeoutMs = 10000
		bindTransportRetry(input)
		start := time.Now()
		result := adapter.Execute(context.Background(), input)
		if resetRequests.Load() != 2 || time.Since(start) < 500*time.Millisecond {
			t.Fatalf("native reset retry bound/delay differed: requests=%d result=%v", resetRequests.Load(), result)
		}
		if always {
			if failure := result.GetError(); failure == nil || failure.Error.Code != runtimev1.ErrorCode_ERROR_CODE_TRANSPORT {
				t.Fatalf("exhausted reset was accepted: %v", result)
			}
		} else if success := result.GetSuccess(); success == nil || success.GetStatus() != 201 || !strings.Contains(string(bridgeManifestBody(success.Html)), "Recovered Engineer") {
			t.Fatalf("native reset did not recover: %v", result)
		}
	}
}

func bindTransportRetry(input *runtimev1.BrowserExecutionInput) {
	input.Plan.Navigation.TransportRetries = 1
	parent := input.Plan.Navigation.OriginRequestId
	input.Plan.OriginOperations = append(input.Plan.OriginOperations, &runtimev1.OriginOperationRef{OriginRequestId: parent + ":transport-retry-1", OperationSequence: 2, Role: "transport_retry", ParentOriginRequestId: &parent, RequestFingerprint: input.Plan.OriginOperations[0].RequestFingerprint})
}
