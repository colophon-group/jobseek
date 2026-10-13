//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestLightpandaInlineCardsCompleteIdentityAndPublisherBoundary(t *testing.T) {
	binary := integrationBinary(t)
	for _, mode := range []string{"isolated-five", "identity-shift", "duplicate-identity", "missing-detail", "publisher-before", "publisher-after"} {
		t.Run(mode, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			var navigations atomic.Int32
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := navigations.Add(1)
				if mode == "publisher-before" {
					w.Header().Set("Tdm-Reservation", "1")
				}
				w.Header().Set("Content-Type", "text/html")
				var body strings.Builder
				body.WriteString(`<!doctype html><html><head></head><body><section id="detail"></section>`)
				for i := 0; i < 5; i++ {
					identity := fmt.Sprintf("card%d", i)
					if mode == "identity-shift" && n > 1 && i == 0 {
						identity = "changed"
					}
					if mode == "duplicate-identity" {
						identity = "duplicate"
					}
					action := fmt.Sprintf(`document.getElementById('detail').innerHTML='<h2>Engineer %d</h2><p>Complete description %d</p>';`, i, i)
					if mode == "missing-detail" {
						action = ""
					}
					if mode == "publisher-after" {
						action += `const meta=document.createElement('meta');meta.name='tdm-reservation';meta.content='1';document.head.appendChild(meta);`
					}
					body.WriteString(fmt.Sprintf(`<label for="%s-row">Card</label><button class="card" onclick="%s">Open</button>`, identity, action))
				}
				body.WriteString(`<script>const original=document.querySelectorAll;document.querySelectorAll=function(selector){if(selector==='button.card'||selector==='label'||selector==='#detail')return [];return original.call(this,selector)};</script></body></html>`)
				_, _ = io.WriteString(w, body.String())
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
			run := func(ctx context.Context, c Config, task Task) (Result, error) {
				return runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: run}
			input, err := lp.NavigationInput(lp.Navigation{URL: "https://jobs.dayforcehcm.com/cards", RoutingRevision: "card-fixture", OriginRequestID: "card-origin", Wait: "domcontentloaded", TimeoutMS: 5000})
			if err != nil {
				t.Fatal(err)
			}
			payload, _ := proto.Marshal(input)
			cards := &actions.InlineCards{ControlSelector: "button.card", ContentSelector: "#detail", IdentitySelector: "label", IdentityAttribute: "for", IdentityPattern: `([A-Za-z0-9]+)-row`, Maximum: 5, TimeoutMS: 5000}
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			response, err := execution.executeDocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: payload, InlineCards: cards})
			if err != nil {
				t.Fatal(err)
			}
			result := new(runtimev1.BrowserResult)
			if proto.Unmarshal(response.Result, result) != nil {
				t.Fatal("invalid typed result")
			}
			failure := mode == "identity-shift" || mode == "duplicate-identity" || mode == "missing-detail"
			if failure {
				if result.GetError() == nil || len(response.InlineIdentities) != 0 {
					t.Fatal("partial or shifted card collection gained authority")
				}
				return
			}
			if result.GetSuccess() == nil {
				t.Fatal("card collection failed", result.GetError())
			}
			var html strings.Builder
			for _, chunk := range result.GetSuccess().Html.Chunks {
				html.Write(chunk.GetInlineBody())
			}
			if strings.HasPrefix(mode, "publisher-") {
				if len(response.InlineIdentities) != 0 || strings.Contains(html.String(), "jobseek-inline-detail") {
					t.Fatal("publisher reservation emitted expanded cards")
				}
				if navigations.Load() != 1 {
					t.Fatal("reservation triggered another root navigation")
				}
				return
			}
			expected := []string{"card0", "card1", "card2", "card3", "card4"}
			got, _ := json.Marshal(response.InlineIdentities)
			want, _ := json.Marshal(expected)
			if string(got) != string(want) || navigations.Load() != 5 || strings.Count(html.String(), "<jobseek-inline-detail>") != 5 {
				t.Fatal("incomplete expanded collection", string(got), navigations.Load())
			}
			for i := 0; i < 5; i++ {
				if !strings.Contains(html.String(), fmt.Sprintf("Complete description %d", i)) {
					t.Fatal("wrong expanded fragment")
				}
			}
		})
	}
}
