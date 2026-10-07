//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLightpandaDocumentActionsIntegration(t *testing.T) {
	binary := integrationBinary(t)
	for _, mode := range []string{"sequential-async-optional", "required-failure", "publisher-before-actions", "publisher-after-action"} {
		t.Run(mode, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "publisher-before-actions" {
					w.Header().Set("Tdm-Reservation", "1")
				}
				w.Header().Set("Content-Type", "text/html")
				_, _ = io.WriteString(w, `<!doctype html><html><body><h2>Initial</h2></body></html>`)
			}))
			listener, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxy(t, origin)
			fixture := newServiceTLSFixture(t)
			run := func(ctx context.Context, c Config, task Task) (Result, error) {
				c.Binary = binary
				c.EgressPolicy = defaultEgressPolicy()
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, run)
			if err != nil {
				t.Fatal(err)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
			defer stop()
			client := dayforceClientFixture(t, fixture, address)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			input, err := lp.NavigationInput(lp.Navigation{URL: "https://jobs.dayforcehcm.com/document-actions", RoutingRevision: "document-actions-fixture", OriginRequestID: "document-actions-origin", Wait: "load", TimeoutMS: 5000, TransportRetries: 1})
			if err != nil {
				t.Fatal(err)
			}
			pipeline := []actions.Action{
				{Kind: "evaluate", Script: `() => { throw new Error('private optional exception') }`, TimeoutMS: 500, Required: mode == "required-failure"},
				{Kind: "evaluate", Script: `async () => { await new Promise(r => setTimeout(r,5)); document.body.innerHTML += '<article><h2>Rendered Engineer</h2><a href="/jobs/one">One</a></article>'; }`, TimeoutMS: 1000, Required: true},
				{Kind: "wait", Milliseconds: 5, TimeoutMS: 500},
			}
			if mode == "publisher-after-action" {
				pipeline = []actions.Action{{Kind: "evaluate", Script: `() => { const m=document.createElement('meta');m.name='tdm-reservation';m.content='1';document.head.appendChild(m); }`, TimeoutMS: 1000, Required: true}, {Kind: "evaluate", Script: `() => document.body.innerHTML += 'SHOULD_NOT_RUN'`, TimeoutMS: 1000, Required: true}}
			}
			payload, err := proto.Marshal(input)
			if err != nil {
				t.Fatal(err)
			}
			held, err := client.Reserve(ctx)
			if err != nil {
				t.Fatal(err)
			}
			response, err := held.DocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: payload, Actions: pipeline})
			if err != nil {
				t.Fatal("document action service/client failed", err)
			}
			result := new(runtimev1.BrowserResult)
			if proto.Unmarshal(response.Result, result) != nil {
				t.Fatal("invalid typed result")
			}
			if mode == "required-failure" {
				if result.GetError() == nil || result.GetSuccess() != nil {
					t.Fatal("required failure published partial document")
				}
				return
			}
			success := result.GetSuccess()
			if success == nil {
				t.Fatal("real action fixture failed", result.GetError())
				return
			}
			var html strings.Builder
			for _, chunk := range success.Html.Chunks {
				html.Write(chunk.GetInlineBody())
			}
			if mode == "sequential-async-optional" && !strings.Contains(html.String(), "Rendered Engineer") {
				t.Fatal("async mutation or optional continuation missing")
			}
			if mode == "publisher-before-actions" && strings.Contains(html.String(), "Rendered Engineer") {
				t.Fatal("initial policy ignored")
			}
			if mode == "publisher-after-action" && (!strings.Contains(html.String(), "tdm-reservation") || strings.Contains(html.String(), "SHOULD_NOT_RUN")) {
				t.Fatal("later policy did not stop pipeline")
			}
			if strings.Contains(html.String(), "private optional exception") {
				t.Fatal("exception leaked into document")
			}
		})
	}
}
