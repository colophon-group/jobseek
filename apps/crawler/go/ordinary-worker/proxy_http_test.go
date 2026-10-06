package worker

import (
	"bufio"
	"context"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

func proxyHTTPFixture(t *testing.T, handlers ...http.HandlerFunc) (*VerifiedHTTP, *rotatingProxyTransport) {
	t.Helper()
	endpoints := []*url.URL{}
	for _, handler := range handlers {
		server := httptest.NewServer(handler)
		t.Cleanup(server.Close)
		u, _ := url.Parse(server.URL)
		u.User = url.UserPassword("synthetic-user", "synthetic-password")
		endpoints = append(endpoints, u)
	}
	verified, err := newVerifiedProxyHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: []string{"127.0.0.1"}}, proxyRuntimeConfig{enabled: true, endpoints: endpoints, forced: -1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(verified.CloseIdleConnections)
	transport := verified.client.Transport.(*rotatingProxyTransport)
	transport.base.lookup = func(_ context.Context, host string) ([]netip.Addr, error) {
		if host == "private.example" {
			return []netip.Addr{netip.MustParseAddr("127.0.0.1")}, nil
		}
		return []netip.Addr{netip.MustParseAddr("93.184.216.34")}, nil
	}
	return verified, transport
}

func TestProxyHTTPHTTPSProxyRequiresVerifiedEndpointTrust(t *testing.T) {
	proxy := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Proxy-Authorization") == "" {
			t.Error("HTTPS proxy lost protected auth")
		}
		_, _ = io.WriteString(w, "ok")
	}))
	t.Cleanup(proxy.Close)
	endpoint, _ := url.Parse(proxy.URL)
	endpoint.User = url.UserPassword("synthetic-user", "synthetic-password")
	for _, trusted := range []bool{false, true} {
		t.Run(fmt.Sprint(trusted), func(t *testing.T) {
			roots := pinnedCA
			if trusted {
				roots = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: proxy.Certificate().Raw})
			}
			verified, err := newVerifiedProxyHTTP(DirectHTTPConfig{CABundlePEM: roots, InternalHosts: []string{"127.0.0.1"}}, proxyRuntimeConfig{enabled: true, endpoints: []*url.URL{endpoint}, forced: -1})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(verified.CloseIdleConnections)
			p := verified.client.Transport.(*rotatingProxyTransport)
			p.base.lookup = func(context.Context, string) ([]netip.Addr, error) {
				return []netip.Addr{netip.MustParseAddr("93.184.216.34")}, nil
			}
			response, err := verified.client.Get("http://public.example/")
			if !trusted {
				if err == nil {
					response.Body.Close()
					t.Fatal("untrusted HTTPS proxy was accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(response.Body)
			response.Body.Close()
			if err != nil || string(body) != "ok" {
				t.Fatal("trusted HTTPS proxy did not complete")
			}
		})
	}
}

func TestProxyHTTPSOCKS5AuthenticatedHopDoesNotExposeCredentialsToOrigin(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	done := make(chan error, 1)
	go func() {
		connection, err := listener.Accept()
		if err != nil {
			done <- err
			return
		}
		defer connection.Close()
		connection.SetDeadline(time.Now().Add(3 * time.Second))
		r := bufio.NewReader(connection)
		read := func(n int) ([]byte, error) { b := make([]byte, n); _, e := io.ReadFull(r, b); return b, e }
		greeting, err := read(2)
		if err != nil || greeting[0] != 5 {
			done <- errors.New("invalid SOCKS greeting")
			return
		}
		methods, err := read(int(greeting[1]))
		if err != nil || !strings.ContainsRune(string(methods), 2) {
			done <- errors.New("SOCKS auth not offered")
			return
		}
		connection.Write([]byte{5, 2})
		auth, err := read(2)
		if err != nil || auth[0] != 1 {
			done <- errors.New("invalid SOCKS auth")
			return
		}
		username, err := read(int(auth[1]))
		if err != nil {
			done <- err
			return
		}
		length, err := read(1)
		if err != nil {
			done <- err
			return
		}
		password, err := read(int(length[0]))
		if err != nil {
			done <- err
			return
		}
		if string(username) != "synthetic-user" || string(password) != "synthetic-password" {
			done <- errors.New("SOCKS credentials differ")
			return
		}
		connection.Write([]byte{1, 0})
		target, err := read(5)
		if err != nil || target[0] != 5 || target[1] != 1 || target[3] != 3 {
			done <- errors.New("invalid SOCKS target")
			return
		}
		host, err := read(int(target[4]))
		if err != nil {
			done <- err
			return
		}
		port, err := read(2)
		if err != nil || string(host) != "public.example" || port[0] != 0 || port[1] != 80 {
			done <- errors.New("SOCKS target changed")
			return
		}
		connection.Write([]byte{5, 0, 0, 1, 127, 0, 0, 1, 0, 80})
		request, err := http.ReadRequest(r)
		if err != nil || request.Host != "public.example" || request.Header.Get("Proxy-Authorization") != "" {
			done <- errors.New("SOCKS origin leaked credentials or changed")
			return
		}
		_, err = io.WriteString(connection, "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
		done <- err
	}()
	endpoint, _ := url.Parse("socks5://" + listener.Addr().String())
	endpoint.User = url.UserPassword("synthetic-user", "synthetic-password")
	verified, err := newVerifiedProxyHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: []string{"127.0.0.1"}}, proxyRuntimeConfig{enabled: true, endpoints: []*url.URL{endpoint}, forced: -1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(verified.CloseIdleConnections)
	p := verified.client.Transport.(*rotatingProxyTransport)
	p.base.lookup = func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("93.184.216.34")}, nil
	}
	response, err := verified.client.Get("http://public.example/")
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil || string(body) != "ok" {
		t.Fatal("authenticated SOCKS hop did not complete")
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestProxyHTTPConnectionBudgetEvictsIdleAcrossPoolSlots(t *testing.T) {
	handler := func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, "ok") }
	verified, p := proxyHTTPFixture(t, handler, handler, handler)
	p.base.requests, p.base.connections = make(chan struct{}, 2), make(chan struct{}, 2)
	p.base.timeout = 300 * time.Millisecond
	for i := 0; i < 4; i++ {
		response, err := verified.client.Get("http://public.example/")
		if err != nil {
			t.Fatal("cross-slot idle connections stranded the shared budget", err)
		}
		_, err = io.Copy(io.Discard, response.Body)
		_ = response.Body.Close()
		if err != nil || len(p.base.connections) > 2 {
			t.Fatal("proxy connection budget changed", err)
		}
	}
}

func TestProxyHTTPRedirectAffinityRotationCookiesAndAccounting(t *testing.T) {
	var mu sync.Mutex
	visits := []int{}
	var transport *rotatingProxyTransport
	handler := func(slot int) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			visits = append(visits, slot)
			mu.Unlock()
			if r.Header.Get("Proxy-Authorization") != "Basic c3ludGhldGljLXVzZXI6c3ludGhldGljLXBhc3N3b3Jk" {
				t.Error("configured proxy auth absent")
			}
			if r.URL.Path == "/start" {
				http.SetCookie(w, &http.Cookie{Name: "flow", Value: "same-exit", Path: "/"})
				w.Header().Set("Location", "http://public.example/final")
				w.WriteHeader(302)
				return
			}
			if r.URL.Path == "/final" {
				if c, err := r.Cookie("flow"); err != nil || c.Value != "same-exit" {
					t.Error("redirect lost cookie")
				}
				transport.pool.mu.Lock()
				if transport.pool.global[slot].failures != 1 || !transport.pool.global[slot].inFlight {
					t.Error("intermediate redirect falsely recovered probe")
				}
				transport.pool.mu.Unlock()
			}
			_, _ = io.WriteString(w, "ok")
		}
	}
	verified, p := proxyHTTPFixture(t, handler(0), handler(1))
	transport = p
	now := 0.0
	p.pool.now = func() float64 { return now }
	s, _ := p.pool.selectEndpoint("public.example")
	p.pool.failure(s, "public.example", "proxy_transport")
	now = 120
	ctx, observation := ObserveHTTP(context.Background())
	request, _ := http.NewRequestWithContext(ctx, "GET", "http://public.example/start", nil)
	response, err := verified.client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, err = io.ReadAll(response.Body)
	_ = response.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if p.pool.global[0].failures != 0 {
		t.Fatal("final body did not recover owned probe")
	}
	response, err = verified.client.Get("http://public.example/next")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	mu.Lock()
	defer mu.Unlock()
	if len(visits) != 3 || visits[0] != 0 || visits[1] != 0 || visits[2] != 1 {
		t.Fatalf("redirect changed slot or next request did not rotate: %v", visits)
	}
	snapshot := observation.Snapshot()
	if snapshot.Requests != 2 || snapshot.Responses != 2 || snapshot.EncodedBytes != 2 || snapshot.NoResponse != 0 {
		t.Fatalf("origin/byte conservation differs: %+v", snapshot)
	}
}

func TestProxyHTTPDenialProviderErrorsAndIncompleteStreams(t *testing.T) {
	for _, mode := range []string{"origin-denial", "provider-auth", "provider-header", "truncated", "abandon", "private"} {
		t.Run(mode, func(t *testing.T) {
			contacts := 0
			verified, p := proxyHTTPFixture(t, func(w http.ResponseWriter, r *http.Request) {
				contacts++
				switch mode {
				case "origin-denial":
					w.WriteHeader(403)
				case "provider-auth":
					w.WriteHeader(407)
				case "provider-header":
					w.Header().Set("X-Webshare-Error-Reason", "synthetic-private-diagnostic")
					w.WriteHeader(502)
				case "truncated":
					w.Header().Set("Content-Length", "100")
					_, _ = io.WriteString(w, "short")
				default:
					_, _ = io.WriteString(w, "ok")
				}
			})
			origin := "public.example"
			if mode == "private" {
				origin = "private.example"
			}
			response, err := verified.client.Get("http://" + origin + "/")
			if mode == "private" {
				if !errors.Is(err, ErrUnsafeURL) || contacts != 0 {
					t.Fatal("proxy bypassed origin public-address guard")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if mode != "abandon" {
				_, _ = io.ReadAll(response.Body)
			}
			_ = response.Body.Close()
			g, o := p.pool.global[0], p.pool.origin(0, origin, false)
			switch mode {
			case "origin-denial", "truncated":
				if g.failures != 0 || o == nil || o.failures != 1 {
					t.Fatal("target failure quarantined wrong circuit")
				}
			case "provider-auth", "provider-header":
				if g.failures != 1 || o != nil {
					t.Fatal("provider failure was not global")
				}
			case "abandon":
				if g.failures != 0 || o != nil {
					t.Fatal("incomplete stream declared a failure")
				}
			}
		})
	}
}

func TestProxyHTTPTunnelUsesPinnedTrustAndKeepsCredentialsFromOrigin(t *testing.T) {
	for _, status := range []int{200, 407, 502} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			originContacts := 0
			origin := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				originContacts++
				if r.Header.Get("Proxy-Authorization") != "" {
					t.Error("proxy credentials reached origin")
				}
				_, _ = io.WriteString(w, "verified tunnel")
			}))
			t.Cleanup(origin.Close)
			verified, p := proxyHTTPFixture(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "CONNECT" || !strings.HasPrefix(r.Host, "example.com:") {
					t.Error("invalid CONNECT target")
				}
				if status != 200 {
					w.WriteHeader(status)
					return
				}
				target, err := net.Dial("tcp", strings.TrimPrefix(origin.URL, "https://"))
				if err != nil {
					t.Error(err)
					return
				}
				client, buffered, err := w.(http.Hijacker).Hijack()
				if err != nil {
					target.Close()
					t.Error(err)
					return
				}
				_, _ = buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
				_ = buffered.Flush()
				go func() { _, _ = io.Copy(target, buffered); _ = target.Close() }()
				go func() { _, _ = io.Copy(client, target); _ = client.Close() }()
			})
			// Fixture trust is still verified; no insecure TLS fallback.
			roots := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: origin.Certificate().Raw})
			base, err := NewDirectHTTP(DirectHTTPConfig{CABundlePEM: roots, InternalHosts: []string{"127.0.0.1"}})
			if err != nil {
				t.Fatal(err)
			}
			newBase := base.Transport.(*directTransport)
			newBase.lookup = p.base.lookup
			p.base = newBase
			_, port, _ := net.SplitHostPort(strings.TrimPrefix(origin.URL, "https://"))
			response, err := verified.client.Get("https://example.com:" + port + "/")
			if status != 200 {
				if err == nil || p.pool.global[0].failures != 1 || originContacts != 0 || strings.Contains(err.Error(), "synthetic-password") {
					t.Fatal("failed CONNECT was not contained/sanitized")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(response.Body)
			_ = response.Body.Close()
			if err != nil || string(body) != "verified tunnel" || originContacts != 1 {
				t.Fatal("verified tunnel did not complete")
			}
		})
	}
}

func TestProxyHTTPProtectedConfig(t *testing.T) {
	for _, c := range []struct {
		provider, urls, legacy, slot string
		valid                        bool
		count                        int
	}{
		{"none", "", "", "", true, 0},
		{"webshare", `["http://u:p@p.webshare.io:80/"]`, "", "0", true, 1},
		{"webshare", `["https://u:p@p.webshare.io:443","socks5://u:p@p.webshare.io:1080"]`, "", "1", true, 2},
		{"webshare", `[]`, "http://u:p@192.0.2.1:80", "0", true, 1},
		{"unknown", "", "", "", false, 0},
		{"webshare", `["http://u:p@other.invalid:80"]`, "", "", false, 0},
		{"webshare", `["http://u:p@p.webshare.io:80","http://u:p@p.webshare.io:80/"]`, "", "", false, 0},
		{"webshare", `["http://u@p.webshare.io:80"]`, "", "", false, 0},
		{"webshare", `["http://u:p@p.webshare.io:80/path"]`, "", "", false, 0},
		{"webshare", `["http://u:p@p.webshare.io:80"]`, "", "1", false, 0},
	} {
		env := map[string]string{"PROXY_PROVIDER": c.provider, "WEBSHARE_PROXY_URLS": c.urls, "WEBSHARE_PROXY_URL": c.legacy, "WEBSHARE_PROXY_CANARY_SLOT": c.slot}
		got, err := readProxyRuntimeConfig(func(k string) string { return env[k] })
		if (err == nil) != c.valid || err == nil && len(got.endpoints) != c.count {
			t.Fatalf("protected endpoint validation differs: valid=%v count=%d", err == nil, len(got.endpoints))
		}
	}
}
