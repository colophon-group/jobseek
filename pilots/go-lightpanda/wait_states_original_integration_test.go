//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"testing"
	"time"

	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
)

func TestLightpandaOriginalWaitStateFixtures(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	raw, err := os.ReadFile("testdata/python_wait_states.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Name, HTML string
		Actions    any
		Success    bool
	}
	if json.Unmarshal(raw, &rows) != nil || len(rows) != 21 {
		t.Fatal("original wait references unavailable")
	}
	address := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		address = "127.0.0.1"
	}
	for _, row := range rows {
		t.Run(row.Name, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				io.WriteString(w, "<!doctype html><html><body>"+row.HTML+"</body></html>")
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
			pipeline, err := actions.Parse(row.Actions)
			if err != nil {
				t.Fatal("original valid action was not admitted", err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			_, err = runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: "https://jobs.dayforcehcm.com/role", Actions: pipeline})
			if (err == nil) != row.Success {
				t.Fatal("native wait differs from original reference", err)
			}
		})
	}
}
