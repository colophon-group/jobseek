//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"

	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
)

func TestLargeDocumentPhysicalWireKeepsCompleteHTMLAndLegacyLimit(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	loopback := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		loopback = "127.0.0.1"
	}
	for _, large := range []bool{false, true} {
		t.Run(map[bool]string{false: "legacy", true: "large"}[large], func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "text/html")
				_, _ = io.WriteString(w, "<!doctype html><html><body><div>"+strings.Repeat("x", 2_800_000)+`</div><a href="/jobs/last">Final posting</a></body></html>`)
			}))
			listener, err := net.Listen("tcp4", loopback+":0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxyAt(t, origin, loopback)
			fixture := newServiceTLSFixture(t)
			run := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary, c.EgressPolicy = binary, defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: loopback}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, run)
			if err != nil {
				t.Fatal(err)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
			defer stop()
			client := dayforceClientFixture(t, fixture, address)
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			input, err := lp.NavigationInput(lp.Navigation{URL: "https://jobs.dayforcehcm.com/large-document", RoutingRevision: "large-document", OriginRequestID: "large-document", Wait: "load", TimeoutMS: 10000})
			if err != nil {
				t.Fatal(err)
			}
			payload, _ := proto.Marshal(input)
			request := actions.Request{Protocol: actions.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: payload, LargeDocument: large}
			if !large {
				request.Actions = []actions.Action{{Kind: "wait", TimeoutMS: 1000}}
			}
			held, err := client.Reserve(ctx)
			if err != nil {
				t.Fatal(err)
			}
			response, err := held.DocumentActions(ctx, request)
			if err != nil {
				t.Fatal("physical document session failed", err)
			}
			result := new(runtimev1.BrowserResult)
			if proto.Unmarshal(response.Result, result) != nil {
				t.Fatal("invalid typed document")
			}
			if !large {
				if result.GetSuccess() != nil || result.GetError().GetError().GetCode() != runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT {
					t.Fatal("legacy result ceiling changed")
				}
				return
			}
			if result.GetSuccess() == nil || result.GetSuccess().Html.TotalSizeBytes < 2_800_000 {
				t.Fatal("large physical document was withheld or truncated")
			}
			var body strings.Builder
			for _, chunk := range result.GetSuccess().Html.Chunks {
				body.Write(chunk.GetInlineBody())
			}
			if !strings.Contains(body.String(), `href="/jobs/last"`) {
				t.Fatal("posting beyond original document limit was lost")
			}
		})
	}
}
