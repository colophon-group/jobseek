//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"testing"
	"time"
)

type heapFixtureStarter struct {
	dayforceFixtureStarter
	started managedProcess
}

func (s *heapFixtureStarter) Start(port int) (managedProcess, error) {
	p, err := s.dayforceFixtureStarter.Start(port)
	s.started = p
	return p, err
}

// Exercise the pinned browser's real heap limit, reap the failed child, then
// prove that an independent subsequent navigation still succeeds.
func TestLightpandaHeapLimitIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	address := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		address = "127.0.0.1"
	}
	certificate, ca := dayforceOriginTLS(t)
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		if r.URL.Path == "/heap" {
			_, _ = io.WriteString(w, `<html><body><script>globalThis.retained=[];for(let i=0;i<64;i++){globalThis.retained.push(new Array(2000000).fill(i))}</script></body></html>`)
			return
		}
		_, _ = io.WriteString(w, `<html><head><title>After heap failure</title></head><body>complete</body></html>`)
	}))
	listener, err := net.Listen("tcp4", address+":0")
	if err != nil {
		t.Fatal(err)
	}
	origin.Listener = listener
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	origin.StartTLS()
	defer origin.Close()
	proxy := dayforceConnectProxyAt(t, origin, address)
	config := Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 15 * time.Second}
	starter := &heapFixtureStarter{dayforceFixtureStarter: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}}
	deps := dependencies{
		process: starter,
		ready:   httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()},
		allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen,
	}
	result, err := runTaskWithDependencies(context.Background(), config, deps, Task{URL: "https://jobs.dayforcehcm.com/heap"})
	if !errors.Is(err, errResourceLimit) || errors.Is(err, errCleanupUnproved) || result.HTMLPresent || result.Status != 0 {
		t.Log("synthetic heap fixture child output:", starter.started.Logs())
		t.Fatalf("heap failure must yield a resource error after proved cleanup, got %v", err)
	}
	result, err = runTaskWithDependencies(context.Background(), config, deps, Task{URL: "https://jobs.dayforcehcm.com/normal"})
	if err != nil || result.Status != http.StatusOK || !result.HTMLPresent {
		t.Fatalf("subsequent independent navigation failed: %v", err)
	}
}
