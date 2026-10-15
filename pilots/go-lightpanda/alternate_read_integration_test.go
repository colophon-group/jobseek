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
	"sync/atomic"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

func TestLightpandaAlternateReadRetainsCanonicalDOMDefaultsIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	address := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		address = "127.0.0.1"
	}
	certificate, ca := dayforceOriginTLS(t)
	var requests atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/print/42" {
			t.Error("renderer requested canonical URL instead of alternate read")
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(w, `<!doctype html><html><body><h1>initial</h1><p>initial</p><script>document.querySelector('h1').textContent='Role '+location.pathname;document.querySelector('p').textContent='Rendered alternate description';</script></body></html>`)
	}))
	listener, err := net.Listen("tcp4", address+":0")
	if err != nil {
		t.Fatal(err)
	}
	origin.Listener = listener
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}}
	origin.StartTLS()
	defer origin.Close()
	proxy := dayforceConnectProxyAt(t, origin, address)
	defer proxy.Close()
	source := "https://jobs.dayforcehcm.com/job/42"
	config := dom.Object{"fetch_url_transform": map[string]any{"find": "/job/", "replace": "/print/"}, "steps": []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "p", "field": "description"}}, "defaults_by_url": map[string]any{source: map[string]any{"employment_type": "FULL_TIME"}}}
	fetchURL, err := dom.AlternateFetchURL(source, config["fetch_url_transform"])
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	result, err := runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: fetchURL, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: 10 * time.Second}})
	if err != nil || result.FinalURL != fetchURL || requests.Load() != 1 {
		t.Fatal("alternate navigation or request conservation differs", err, requests.Load())
	}
	fields, err := dom.Parse(result.HTML, config, &source)
	if err != nil || fields["title"] != "Role /print/42" || fields["description"] != "Rendered alternate description" || fields["employment_type"] != "FULL_TIME" {
		t.Fatal("physical alternate rendering or canonical defaults differ", err, fields)
	}
}
