//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
)

func TestConfiguredBrowserProxyNeverPassesCredentialsInProcessArguments(t *testing.T) {
	endpoint, err := browserproxy.Parse("http://fixture-user:fixture-secret@proxy.example:8000")
	if err != nil {
		t.Fatal(err)
	}
	command, err := (commandStarter{binary: "lightpanda", egressPolicy: defaultEgressPolicy(), proxy: &endpoint}).buildCommand(9222, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	args := strings.Join(command.Args, " ")
	if !strings.Contains(args, "--http-proxy http://proxy.example:8000") || strings.Contains(args, "fixture-user") || strings.Contains(args, "fixture-secret") {
		t.Fatal("proxy process arguments disclose authentication or omit configured endpoint")
	}
	for _, raw := range []string{"[]", "null", "[true]", "[\"socks5://proxy.example\"]", "[\"http://proxy.example\",\"http://proxy.example/\"]"} {
		if _, err := parseBrowserProxies([]byte(raw)); err == nil {
			t.Fatal("invalid or duplicate startup proxy settings accepted")
		}
	}
}

func TestConfiguredBrowserProxyFileRejectsUnsafeStartupObjects(t *testing.T) {
	dir := t.TempDir()
	if proxies, err := readBrowserProxies(filepath.Join(dir, "missing")); err != nil || proxies != nil {
		t.Fatal("optional absent proxy configuration changed direct startup")
	}
	file := filepath.Join(dir, "proxies.json")
	if os.WriteFile(file, []byte(`["http://proxy.example"]`), 0600) != nil {
		t.Fatal("fixture write failed")
	}
	if _, err := readBrowserProxies(file); err != errBrowserProxy {
		t.Fatal("writable proxy settings accepted")
	}
	link := filepath.Join(dir, "link")
	if os.Symlink(file, link) != nil {
		t.Fatal("fixture link failed")
	}
	if _, err := readBrowserProxies(link); err != errBrowserProxy {
		t.Fatal("proxy settings followed a symlink")
	}
	fifo := filepath.Join(dir, "pipe")
	if syscall.Mkfifo(fifo, 0400) != nil {
		t.Fatal("fixture pipe failed")
	}
	if _, err := readBrowserProxies(fifo); err != errBrowserProxy {
		t.Fatal("non-regular proxy settings accepted")
	}
}

func TestConfiguredBrowserProxyServiceRejectsUnknownReferencesBeforeChild(t *testing.T) {
	endpoint, _ := browserproxy.Parse("http://fixture:secret@proxy.example:8000")
	rotated, _ := browserproxy.Parse("http://fixture:rotated@proxy.example:8000")
	proxies := map[string]browserproxy.Endpoint{endpoint.Reference(): endpoint}
	calls := 0
	execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: defaultEgressPolicy(), proxyEndpoints: proxies}, func(_ context.Context, config Config, task Task) (Result, error) {
		calls++
		if config.proxy == nil || config.proxy.Reference() != endpoint.Reference() || task.proxy == nil || task.proxy.Reference() != endpoint.Reference() {
			t.Fatal("child received a different protected endpoint")
		}
		return Result{}, errors.New("fixed fixture failure")
	})
	if err != nil {
		t.Fatal(err)
	}
	// Caller changes cannot rotate the resident service registry after startup.
	delete(proxies, endpoint.Reference())
	for _, ref := range []string{rotated.Reference(), endpoint.Reference()} {
		input, err := lp.NavigationInput(lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000, ProxyPolicyRef: ref})
		if err != nil {
			t.Fatal(err)
		}
		result := execution.Execute(context.Background(), input)
		if ref == rotated.Reference() && (calls != 0 || result.GetError().GetError().GetCode() != runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG) {
			t.Fatal("unknown or rotated credentials reached a child")
		}
	}
	if calls != 1 {
		t.Fatal("configured proxy never reached its bound child")
	}
}

func TestPhysicalLightpandaConfiguredProxyAuthenticationAndOriginIsolation(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	for _, mode := range []string{"success", "origin-auth", "bad-auth", "private-target", "private-redirect", "private-subresource"} {
		t.Run(mode, func(t *testing.T) {
			var requests, authenticated, originCredentials, privateRequests atomic.Int64
			proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if r.Header.Get("Authorization") != "" {
					originCredentials.Add(1)
				}
				if r.URL.Hostname() == "10.0.0.1" {
					privateRequests.Add(1)
				}
				if mode == "bad-auth" || r.Header.Get("Proxy-Authorization") != "Basic "+base64.StdEncoding.EncodeToString([]byte("fixture-user:fixture-secret")) {
					w.Header().Set("Proxy-Authenticate", `Basic realm="fixture"`)
					w.WriteHeader(407)
					return
				}
				authenticated.Add(1)
				if mode == "private-redirect" {
					w.Header().Set("Location", "http://10.0.0.1/private")
					w.WriteHeader(302)
					return
				}
				if mode == "origin-auth" {
					w.Header().Set("WWW-Authenticate", `Basic realm="origin"`)
					w.WriteHeader(401)
					return
				}
				w.Header().Set("Content-Type", "text/html")
				if mode == "private-subresource" {
					_, _ = io.WriteString(w, `<html><script src="http://10.0.0.1/private"></script></html>`)
					return
				}
				_, _ = io.WriteString(w, "<html><body><h1>Proxy engineer</h1></body></html>")
			}))
			defer proxy.Close()
			endpoint, err := browserproxy.Parse(strings.Replace(proxy.URL, "http://", "http://fixture-user:fixture-secret@", 1))
			if err != nil {
				t.Fatal(err)
			}
			_, ca := dayforceOriginTLS(t, "93.184.216.34")
			config := Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 15 * time.Second, proxy: &endpoint}
			source := "http://93.184.216.34/proxy-job"
			if mode == "private-target" {
				source = "http://10.0.0.1/private"
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			result, err := runTaskWithDependencies(ctx, config, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: endpoint.Address(), ca: ca, fixtureLoopback: "127.0.0.1"}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: source, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_LOAD, timeout: 10 * time.Second}})
			if originCredentials.Load() != 0 {
				t.Fatal("proxy authentication leaked to an origin challenge")
			}
			if strings.HasPrefix(mode, "private-") {
				if privateRequests.Load() != 0 || err == nil {
					t.Fatalf("private destination result: proxy_requests=%d blocked_target_requests=%d status=%d error=%t", requests.Load(), privateRequests.Load(), result.Status, err != nil)
				}
				return
			}
			if mode == "bad-auth" {
				if requests.Load() > 3 || (err == nil && result.Status == 200) {
					t.Fatalf("proxy repeated challenge: requests=%d status=%d error=%t", requests.Load(), result.Status, err != nil)
				}
				return
			}
			if authenticated.Load() < 1 {
				t.Fatal("physical proxy authentication did not occur")
			}
			if mode == "success" && (err != nil || result.Status != 200 || !strings.Contains(result.HTML, "Proxy engineer")) {
				t.Fatal("physical authenticated proxy did not render complete content")
			}
			if requests.Load() > 6 {
				t.Fatal("proxy challenge loop exceeded bounded fixture requests")
			}
		})
	}
}

func TestPhysicalLightpandaConfiguredProxyHTTPSConnectAuthentication(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	certificate, ca := dayforceOriginTLS(t, "93.184.216.34")
	var originCalls, originCredentials, connectAuth, connectCalls atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		originCalls.Add(1)
		if r.Header.Get("Authorization") != "" || r.Header.Get("Proxy-Authorization") != "" {
			originCredentials.Add(1)
		}
		w.Header().Set("Content-Type", "text/html")
		_, _ = io.WriteString(w, "<html><body>Verified HTTPS proxy job</body></html>")
	}))
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	origin.StartTLS()
	defer origin.Close()
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		connectCalls.Add(1)
		if r.Method != "CONNECT" || r.Host != "93.184.216.34:443" {
			w.WriteHeader(403)
			return
		}
		if r.Header.Get("Proxy-Authorization") != "Basic "+base64.StdEncoding.EncodeToString([]byte("fixture-user:fixture-secret")) {
			w.Header().Set("Proxy-Authenticate", `Basic realm="fixture"`)
			w.WriteHeader(407)
			return
		}
		connectAuth.Add(1)
		upstream, err := net.DialTimeout("tcp", origin.Listener.Addr().String(), time.Second)
		if err != nil {
			w.WriteHeader(502)
			return
		}
		defer upstream.Close()
		connection, buffer, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer connection.Close()
		_ = connection.SetDeadline(time.Now().Add(15 * time.Second))
		_ = upstream.SetDeadline(time.Now().Add(15 * time.Second))
		_, _ = buffer.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		if buffer.Flush() != nil {
			return
		}
		finished := make(chan struct{})
		go func(reader *bufio.Reader) { _, _ = io.Copy(upstream, reader); _ = upstream.Close(); close(finished) }(buffer.Reader)
		_, _ = io.Copy(connection, upstream)
		_ = connection.Close()
		<-finished
	}))
	defer proxy.Close()
	endpoint, err := browserproxy.Parse(strings.Replace(proxy.URL, "http://", "http://fixture-user:fixture-secret@", 1))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	config := Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 15 * time.Second, proxy: &endpoint}
	result, err := runTaskWithDependencies(ctx, config, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: endpoint.Address(), ca: ca, fixtureLoopback: "127.0.0.1"}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: "https://93.184.216.34/job", Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_LOAD, timeout: 10 * time.Second}})
	if err != nil || result.Status != 200 || !strings.Contains(result.HTML, "Verified HTTPS proxy job") || originCalls.Load() < 1 || connectAuth.Load() < 1 || originCredentials.Load() != 0 {
		t.Fatalf("HTTPS proxy qualification failed: status=%d error=%t origin_calls=%d authenticated_connects=%d credential_leaks=%d connect_calls=%d", result.Status, err != nil, originCalls.Load(), connectAuth.Load(), originCredentials.Load(), connectCalls.Load())
	}
}
