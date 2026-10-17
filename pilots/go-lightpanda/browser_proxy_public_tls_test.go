//go:build !densitybench

package main

import (
	"context"
	"encoding/base64"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

// Explicit qualification only: normal CI remains hermetic. This complements
// the untrusted-origin fixture with a real public, normally verified TLS chain.
func TestPhysicalLightpandaConfiguredProxyPublicVerifiedTLS(t *testing.T) {
	if os.Getenv("LIGHTPANDA_PROXY_PUBLIC_TLS") != "1" {
		t.Skip("requires explicit public TLS qualification")
	}
	binary := interactionIntegrationBinary(t)
	var connects atomic.Int64
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, port, err := net.SplitHostPort(r.Host)
		expected := "Basic " + base64.StdEncoding.EncodeToString([]byte("fixture-user:fixture-secret"))
		if r.Method != http.MethodConnect || err != nil || net.ParseIP(host) == nil || port != "443" || r.Header.Get("Proxy-Authorization") != expected {
			t.Error("verified TLS hop lost numeric pin or proxy authentication")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		upstream, err := net.DialTimeout("tcp", r.Host, 5*time.Second)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		defer upstream.Close()
		child, buffered, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer child.Close()
		child.SetDeadline(time.Now().Add(15 * time.Second))
		upstream.SetDeadline(time.Now().Add(15 * time.Second))
		if _, err := buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil || buffered.Flush() != nil {
			return
		}
		connects.Add(1)
		done := make(chan struct{})
		go func() { io.Copy(upstream, buffered); upstream.Close(); close(done) }()
		io.Copy(child, upstream)
		child.Close()
		upstream.Close()
		<-done
	}))
	defer proxy.Close()
	endpoint, err := browserproxy.Parse(strings.Replace(proxy.URL, "http://", "http://fixture-user:fixture-secret@", 1))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	config := Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 15 * time.Second, proxy: &endpoint}
	result, err := runTaskWithDependencies(ctx, config, dependencies{
		process: commandStarter{binary: binary, egressPolicy: defaultEgressPolicy()}, ready: httpReadyWaiter{interval: defaultReadyInterval},
		executor:     chromedpExecutor{egressPolicy: defaultEgressPolicy(), proxyTransport: pinnedFixtureTransport("")},
		allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen,
	}, Task{URL: "https://example.com/"})
	if err != nil || result.Status != 200 || !strings.Contains(result.HTML, "Example Domain") || connects.Load() == 0 {
		t.Fatalf("verified public TLS unavailable: status=%d authenticated_connects=%d error=%v", result.Status, connects.Load(), err)
	}
}
