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
	"sync/atomic"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

func TestLightpandaFragmentNavigationPreservesRenderedFieldsIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	address := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		address = "127.0.0.1"
	}
	certificate, ca := dayforceOriginTLS(t)
	var requests atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/role" || r.URL.Fragment != "" {
			t.Error("fixture received an unexpected origin request")
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(w, `<!doctype html><html><head><script id="posting" type="application/ld+json">{"@type":"JobPosting","title":"initial"}</script></head><body><h1>initial</h1><p id="description">initial</p><script>const route=location.hash.slice(1);document.querySelector('h1').textContent='Role '+route;document.querySelector('#description').textContent='Details '+route;document.querySelector('#posting').textContent=JSON.stringify({'@context':'https://schema.org','@type':'JobPosting',title:'Role '+route,description:'<p>Details '+route+'</p>'});</script></body></html>`)
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

	for index, fragment := range []string{"route-1", "route-2"} {
		source := "https://jobs.dayforcehcm.com/role#" + fragment
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		result, err := runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: source, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: 10 * time.Second}})
		cancel()
		if err != nil || result.FinalURL != source || requests.Load() != int64(index+1) {
			t.Fatal("physical fragment navigation lost source or request conservation", err, result.FinalURL, requests.Load())
		}
		parsedDOM, err := dom.Parse(result.HTML, dom.Object{"steps": []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "p", "field": "description"}}}, &source)
		if err != nil || parsedDOM["title"] != "Role "+fragment {
			t.Fatal("fragment-driven DOM title was not rendered", err)
		}
		description, ok := parsedDOM["description"].(string)
		if !ok || !strings.Contains(description, "Details "+fragment) {
			t.Fatal("fragment-driven DOM description changed")
		}
		parsedJSONLD, err := jsonld.Parse(source, []byte(result.HTML), nil)
		if err != nil || parsedJSONLD["title"] != "Role "+fragment {
			t.Fatal("fragment-driven JSON-LD title was not rendered", err)
		}
		description, ok = parsedJSONLD["description"].(string)
		if !ok || !strings.Contains(description, "Details "+fragment) {
			t.Fatal("fragment-driven JSON-LD description changed")
		}
	}
}
