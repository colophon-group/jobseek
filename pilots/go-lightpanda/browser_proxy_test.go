//go:build !densitybench

package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
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
	command, err := (commandStarter{binary: "lightpanda", egressPolicy: defaultEgressPolicy(), proxy: &endpoint, proxyBridgePort: 23456}).buildCommand(9222, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	args := strings.Join(command.Args, " ")
	if !strings.Contains(args, "--http-proxy "+pinnedProxyBridgeAddress(23456)) || strings.Contains(args, "proxy.example") || strings.Contains(args, "fixture-user") || strings.Contains(args, "fixture-secret") {
		t.Fatal("child can reach an unpinned upstream or receives proxy credentials")
	}
	if proxies, err := parseBrowserProxies([]byte("[]")); err != nil || len(proxies) != 0 {
		t.Fatal("explicit disabled registry was rejected")
	}
	for _, raw := range []string{"null", "[true]", "[\"socks5://proxy.example\"]", "[\"http://proxy.example\",\"http://proxy.example/\"]"} {
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

func TestConfiguredBrowserProxyRunnerUsesTypedHopFailureWithoutProviderText(t *testing.T) {
	endpoint, _ := browserproxy.Parse("http://fixture:secret@proxy.example:8000")
	for _, sample := range []struct {
		text   string
		code   runtimev1.ErrorCode
		status uint32
	}{
		{"net::ERR_PROXY_AUTH_REQUESTED 407", runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS, 407},
		{"Proxy", runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, 0},
		{"CouldntResolveProxy", runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, 0},
		{"net::ERR_TUNNEL_CONNECTION_FAILED", runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, 0},
		{"RecvError", runtimev1.ErrorCode_ERROR_CODE_NAVIGATION, 0},
		{"net::ERR_NETWORK_CHANGED", runtimev1.ErrorCode_ERROR_CODE_NAVIGATION, 0},
		{"unclassified fixture failure", runtimev1.ErrorCode_ERROR_CODE_INTERNAL, 0},
	} {
		execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: defaultEgressPolicy(), proxyEndpoints: map[string]browserproxy.Endpoint{endpoint.Reference(): endpoint}}, func(context.Context, Config, Task) (Result, error) { return Result{}, navigationError(sample.text) })
		if err != nil {
			t.Fatal(err)
		}
		input, _ := lp.NavigationInput(lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000, ProxyPolicyRef: endpoint.Reference()})
		result := execution.Execute(context.Background(), input)
		failure := result.GetError().GetError()
		if failure.GetCode() != sample.code || failure.GetHttpStatus() != sample.status || failure.Message != "lightpanda execution failed" || len(failure.DiagnosticDetails) != 0 {
			t.Fatal("proxy failure lost typed scope or disclosed provider text")
		}
	}
}

func TestPhysicalLightpandaConfiguredProxyAuthenticationAndOriginIsolation(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("pinned proxy child network confinement requires Linux")
	}
	binary := interactionIntegrationBinary(t)
	for _, mode := range []string{"success", "public-subresource", "post-json", "cookies", "publisher", "public-redirect", "gzip", "origin-auth", "bad-auth", "private-target", "private-redirect", "private-subresource"} {
		t.Run(mode, func(t *testing.T) {
			var requests, authenticated, originCredentials, privateRequests atomic.Int64
			var connectCalls atomic.Int64
			proxy := newPinnedFixtureProxy(t, &connectCalls, mode == "bad-auth", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if r.Header.Get("Authorization") != "" {
					originCredentials.Add(1)
				}
				if r.URL.Hostname() == "10.0.0.1" {
					privateRequests.Add(1)
				}
				authenticated.Add(1)
				if mode == "public-redirect" && r.URL.Path == "/proxy-job" {
					w.Header().Set("Location", "/final-job")
					w.WriteHeader(302)
					return
				}
				if mode == "post-json" && r.URL.Path == "/api" {
					body, _ := io.ReadAll(r.Body)
					if r.Method != "POST" || string(body) != `{"page":2}` {
						t.Error("browser POST identity/body changed")
					}
					w.Header().Set("Content-Type", "application/json")
					io.WriteString(w, `{"ok":true}`)
					return
				}
				if mode == "cookies" && r.URL.Path == "/cookie" {
					if r.Header.Get("Cookie") != "fixture_session=one" {
						t.Error("browser session cookie was not retained")
					}
					w.Header().Set("Content-Type", "application/json")
					io.WriteString(w, `{"ok":true}`)
					return
				}
				if mode == "public-subresource" && r.URL.Path == "/asset.js" {
					w.Header().Set("Content-Type", "application/javascript")
					_, _ = io.WriteString(w, `document.body.setAttribute("data-ready", "yes")`)
					return
				}
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
				if mode == "post-json" {
					io.WriteString(w, `<html><body><script>fetch('/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page:2})}).then(r=>r.json()).then(v=>{if(v.ok)document.body.setAttribute('data-ready','yes')})</script></body></html>`)
					return
				}
				if mode == "cookies" {
					w.Header().Set("Set-Cookie", "fixture_session=one; Path=/")
					io.WriteString(w, `<html><body><script>fetch('/cookie').then(r=>r.json()).then(v=>{if(v.ok)document.body.setAttribute('data-ready','yes')})</script></body></html>`)
					return
				}
				if mode == "publisher" {
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://93.184.216.34/policy")
				}
				if mode == "gzip" {
					w.Header().Set("Content-Encoding", "gzip")
					compressed := gzip.NewWriter(w)
					io.WriteString(compressed, "<html><body>Complete compressed proxy content</body></html>")
					compressed.Close()
					return
				}
				if mode == "public-subresource" {
					_, _ = io.WriteString(w, `<html><body><script src="/asset.js"></script><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="></body></html>`)
					return
				}
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
			wait := runtimev1.WaitCondition_WAIT_CONDITION_LOAD
			if mode == "post-json" || mode == "cookies" {
				wait = runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE
			}
			result, err := runTaskWithDependencies(ctx, config, dependencies{process: commandStarter{binary: binary, proxy: &endpoint, egressPolicy: defaultEgressPolicy()}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy(), proxyTransport: pinnedFixtureTransport(ca)}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: source, Navigation: &navigationOptions{wait: wait, timeout: 10 * time.Second}})
			if originCredentials.Load() != 0 {
				t.Fatal("proxy authentication leaked to an origin challenge")
			}
			if strings.HasPrefix(mode, "private-") {
				if mode != "private-target" && connectCalls.Load() == 0 {
					t.Fatal("private follow-up was not reached from a successful public tunnel")
				}
				if privateRequests.Load() != 0 || err == nil {
					t.Fatalf("private destination result: proxy_requests=%d blocked_target_requests=%d status=%d error=%t", requests.Load(), privateRequests.Load(), result.Status, err != nil)
				}
				return
			}
			if mode == "bad-auth" {
				var failure *navigationTransportError
				if !errors.As(err, &failure) || !failure.proxyAuth {
					t.Fatal("upstream407 lost typed proxy authentication failure")
				}
				if connectCalls.Load() == 0 || connectCalls.Load() > 3 || (err == nil && result.Status == 200) {
					t.Fatalf("proxy repeated challenge: requests=%d status=%d error=%t", requests.Load(), result.Status, err != nil)
				}
				return
			}
			if authenticated.Load() < 1 {
				t.Fatalf("physical proxy authentication did not occur: fixture error=%v", err)
			}
			if mode == "success" && (err != nil || result.Status != 200 || !strings.Contains(result.HTML, "Proxy engineer")) {
				t.Fatal("physical authenticated proxy did not render complete content")
			}
			if mode == "public-subresource" && (err != nil || result.Status != 200 || !strings.Contains(result.HTML, `data-ready="yes"`)) {
				t.Fatal("proxy request guard prevented a public script or data resource")
			}
			if (mode == "post-json" || mode == "cookies") && (err != nil || result.Status != 200 || !strings.Contains(result.HTML, `data-ready="yes"`)) {
				t.Fatalf("browser session/body capture failed: fixture error=%v", err)
			}
			if mode == "publisher" && (err != nil || result.ResourcePolicy == nil || result.ResourcePolicy.GetTdmReservationHeader() != "1" || result.ResourcePolicy.GetTdmPolicyHeader() != "https://93.184.216.34/policy") {
				t.Fatal("publisher response headers lost main document correlation")
			}
			if mode == "public-redirect" && (err != nil || !strings.Contains(result.FinalURL, "/final-job") || !strings.Contains(result.HTML, "Proxy engineer")) {
				t.Fatalf("public redirect lost original URL/content: fixture status=%d URL=%s HTMLbytes=%d error=%v", result.Status, result.FinalURL, len(result.HTML), err)
			}
			if mode == "gzip" && (err != nil || !strings.Contains(result.HTML, "Complete compressed proxy content")) {
				t.Fatal("compressed proxy response was not decoded completely")
			}
			if requests.Load() > 6 {
				t.Fatal("proxy challenge loop exceeded bounded fixture requests")
			}
		})
	}
}

func TestPhysicalLightpandaConfiguredProxyHTTPSRejectsUntrustedOrigin(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("pinned proxy child network confinement requires Linux")
	}
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
	result, err := runTaskWithDependencies(ctx, config, dependencies{process: commandStarter{binary: binary, proxy: &endpoint, egressPolicy: defaultEgressPolicy()}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy(), proxyTransport: pinnedFixtureTransport(ca)}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, Task{URL: "https://93.184.216.34/job", Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_LOAD, timeout: 10 * time.Second}})
	if err == nil || result.Status == 200 || originCalls.Load() != 0 || connectAuth.Load() < 1 || originCredentials.Load() != 0 {
		t.Fatalf("HTTPS proxy qualification failed: status=%d error=%v origin_calls=%d authenticated_connects=%d credential_leaks=%d connect_calls=%d", result.Status, err, originCalls.Load(), connectAuth.Load(), originCredentials.Load(), connectCalls.Load())
	}
}

// Only the Go test dependency maps the checked public proxy address to a local
// fixture. The actual child has only the task bridge IP/port available;
// original TLS verification is retained without any certificate exemption.
func pinnedFixtureTransport(ca string) func(browserproxy.Endpoint, EgressPolicy) (*http.Transport, error) {
	return func(endpoint browserproxy.Endpoint, policy EgressPolicy) (*http.Transport, error) {
		transport, err := newPinnedBrowserProxyTransport(endpoint, policy)
		if err != nil {
			return nil, err
		}
		proxy, _ := url.Parse(endpoint.Address())
		var prefixes []netip.Prefix
		for _, raw := range strings.Split(policy.blockCIDRs, ",") {
			prefixes = append(prefixes, netip.MustParsePrefix(raw))
		}
		tunnel := &pinnedBrowserProxyTunnel{endpoint: endpoint, prefixes: prefixes, lookup: func(ctx context.Context, network, host string) ([]netip.Addr, error) {
			if host == proxy.Hostname() {
				return []netip.Addr{netip.MustParseAddr("93.184.216.35")}, nil
			}
			return net.DefaultResolver.LookupNetIP(ctx, network, host)
		}, dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			return (&net.Dialer{Timeout: time.Second}).DialContext(ctx, network, proxy.Host)
		}}
		transport.DialContext = tunnel.connect
		if ca != "" {
			roots := x509.NewCertPool()
			raw, err := os.ReadFile(ca)
			if err != nil || !roots.AppendCertsFromPEM(raw) {
				return nil, errors.New("fixture TLS root unavailable")
			}
			transport.TLSClientConfig.RootCAs = roots
		}
		return transport, nil
	}
}

func newPinnedFixtureProxy(t *testing.T, attempts *atomic.Int64, badAuth bool, origin http.Handler) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts.Add(1)
		host, _, err := net.SplitHostPort(r.Host)
		if r.Method != "CONNECT" || err != nil || net.ParseIP(host) == nil {
			t.Error("upstream received an unpinned hostname")
			w.WriteHeader(403)
			return
		}
		if badAuth || r.Header.Get("Proxy-Authorization") != "Basic "+base64.StdEncoding.EncodeToString([]byte("fixture-user:fixture-secret")) {
			w.WriteHeader(407)
			return
		}
		conn, buffer, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		conn.SetDeadline(time.Now().Add(15 * time.Second))
		buffer.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		if buffer.Flush() != nil {
			return
		}
		request, err := http.ReadRequest(buffer.Reader)
		if err != nil {
			return
		}
		defer request.Body.Close()
		recorder := httptest.NewRecorder()
		origin.ServeHTTP(recorder, request)
		response := recorder.Result()
		defer response.Body.Close()
		response.Write(conn)
	}))
}
