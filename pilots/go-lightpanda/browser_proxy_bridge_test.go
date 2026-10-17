//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"crypto/x509"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

func TestPinnedProxyBridgeStopsForwardingButRetainsPortUntilCleanup(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls atomic.Int64
	transport := &http.Transport{DialContext: func(context.Context, string, string) (net.Conn, error) {
		calls.Add(1)
		return nil, context.Canceled
	}}
	bridge, err := newPinnedBrowserProxyBridge(ctx, transport, cancel)
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	bridge.Stop()
	address, _ := url.Parse(bridge.address)
	if listener, err := net.Listen("tcp4", address.Host); err == nil {
		listener.Close()
		t.Fatal("cancelled task released its child-permitted port before cleanup")
	}
	client := &http.Client{Timeout: time.Second}
	defer client.CloseIdleConnections()
	response, err := client.Get(bridge.address)
	if err != nil {
		t.Fatal("quarantined listener unavailable", err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable || calls.Load() != 0 {
		t.Fatal("cancelled task still forwarded or returned a success")
	}
	bridge.Close()
	listener, err := net.Listen("tcp4", address.Host)
	if err != nil {
		t.Fatal("proved cleanup did not release its reserved port", err)
	}
	listener.Close()
}

func TestPinnedProxyBridgePreservesCookiesRedirectsBodiesAndPolicy(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var attempts, requests atomic.Int64
	proxy := newPinnedFixtureProxy(t, &attempts, false, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Host != "93.184.216.34:80" || r.Header.Get("Proxy-Authorization") != "" || r.Header.Get("X-Discard") != "" || r.Header.Get("Authorization") != "Bearer origin-fixture" {
			t.Error("origin identity, authentication or hop boundary changed")
		}
		switch r.URL.Path {
		case "/start":
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "fixture", Path: "/", HttpOnly: true})
			w.Header().Set("Location", "/final")
			w.WriteHeader(http.StatusFound)
		case "/final":
			cookie, err := r.Cookie("session")
			if err != nil || cookie.Value != "fixture" {
				t.Error("redirect lost native client cookie")
			}
			w.Header().Set("TDM-Reservation", "1")
			w.Header().Set("TDM-Policy", "https://fixture.example/policy")
			io.WriteString(w, "complete inventory")
		case "/body":
			body, err := io.ReadAll(r.Body)
			if err != nil || r.Method != http.MethodPost || string(body) != `{"page":2}` {
				t.Error("request body changed")
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write(body)
		default:
			t.Error("unexpected origin route")
		}
	}))
	defer proxy.Close()
	endpoint, err := browserproxy.Parse(strings.Replace(proxy.URL, "http://", "http://fixture-user:fixture-secret@", 1))
	if err != nil {
		t.Fatal(err)
	}
	_, ca := dayforceOriginTLS(t, "93.184.216.34")
	transport, err := pinnedFixtureTransport(ca)(endpoint, defaultEgressPolicy())
	if err != nil {
		t.Fatal(err)
	}
	bridge, err := newPinnedBrowserProxyBridge(ctx, transport, cancel)
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	address, _ := url.Parse(bridge.address)
	jar, _ := cookiejar.New(nil)
	client := &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(address)}, Jar: jar, Timeout: 3 * time.Second}
	defer client.CloseIdleConnections()
	for _, mode := range []string{"redirect", "post"} {
		method, path, body := http.MethodGet, "/start", ""
		if mode == "post" {
			method, path, body = http.MethodPost, "/body", `{"page":2}`
		}
		request, _ := http.NewRequestWithContext(ctx, method, "http://93.184.216.34:80"+path, strings.NewReader(body))
		request.Header.Set("Authorization", "Bearer origin-fixture")
		request.Header.Set("Proxy-Authorization", "child-value-must-not-reach-origin")
		request.Header.Set("Connection", "X-Discard")
		request.Header.Set("X-Discard", "hop-only")
		response, err := client.Do(request)
		if err != nil {
			t.Fatal("bridge request failed", err)
		}
		content, err := io.ReadAll(response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusOK {
			t.Fatal("complete response unavailable")
		}
		if mode == "redirect" && (response.Request.URL.Path != "/final" || string(content) != "complete inventory" || response.Header.Get("TDM-Reservation") != "1" || response.Header.Get("TDM-Policy") != "https://fixture.example/policy") {
			t.Fatal("redirect, cookie, content or publisher headers changed")
		}
		if mode == "post" && string(content) != body {
			t.Fatal("POST response changed")
		}
	}
	if attempts.Load() != 3 || requests.Load() != 3 || bridge.Err() != nil {
		t.Fatal("unexpected retries or failed bridge")
	}
}

func TestPinnedProxyBridgeCONNECTRetainsEndToEndTLSIdentity(t *testing.T) {
	fixture := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer fixture.Close()
	certificate, err := x509.ParseCertificate(fixture.TLS.Certificates[0].Certificate[0])
	if err != nil || len(certificate.DNSNames) == 0 {
		t.Fatal("origin TLS identity unavailable")
	}
	host := certificate.DNSNames[0]
	roots := x509.NewCertPool()
	roots.AddCert(certificate)
	seenName := make(chan string, 1)
	transport, seen := testPinnedTunnel(t, func(conn net.Conn) {
		config := fixture.TLS.Clone()
		config.GetConfigForClient = func(hello *tls.ClientHelloInfo) (*tls.Config, error) { seenName <- hello.ServerName; return nil, nil }
		secured := tls.Server(conn, config)
		if secured.Handshake() != nil {
			return
		}
		request, err := http.ReadRequest(bufio.NewReader(secured))
		if err != nil || request.Host != host || request.Header.Get("Proxy-Authorization") != "" {
			t.Error("origin TLS/HTTP identity changed")
			return
		}
		io.WriteString(secured, "HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\ntls")
	})
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	bridge, err := newPinnedBrowserProxyBridge(ctx, transport, cancel)
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	address, _ := url.Parse(bridge.address)
	client := &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(address), TLSClientConfig: &tls.Config{RootCAs: roots}}, Timeout: 3 * time.Second}
	defer client.CloseIdleConnections()
	response, err := client.Get("https://" + host + "/job")
	if err != nil {
		t.Fatal("end-to-end TLS tunnel failed", err)
	}
	defer response.Body.Close()
	content, err := io.ReadAll(response.Body)
	if err != nil || string(content) != "tls" || <-seen != "93.184.216.34:443" || <-seenName != host || bridge.Err() != nil {
		t.Fatal("numeric upstream CONNECT or verified origin TLS identity changed")
	}
}

func TestPinnedProxyBridgeRejectsPrivateDestinationBeforeUpstream(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var attempts atomic.Int64
	proxy := newPinnedFixtureProxy(t, &attempts, false, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("private origin reached") }))
	defer proxy.Close()
	endpoint, _ := browserproxy.Parse(strings.Replace(proxy.URL, "http://", "http://fixture-user:fixture-secret@", 1))
	_, ca := dayforceOriginTLS(t, "93.184.216.34")
	transport, err := pinnedFixtureTransport(ca)(endpoint, defaultEgressPolicy())
	if err != nil {
		t.Fatal(err)
	}
	bridge, err := newPinnedBrowserProxyBridge(ctx, transport, cancel)
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	address, _ := url.Parse(bridge.address)
	client := &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(address)}, Timeout: time.Second}
	defer client.CloseIdleConnections()
	response, err := client.Get("http://10.0.0.1/private")
	if response != nil {
		response.Body.Close()
		if response.StatusCode != http.StatusBadGateway {
			t.Fatal("private rejection appeared as a successful inventory")
		}
	}
	if attempts.Load() != 0 || bridge.Err() == nil || (err == nil && response == nil) {
		t.Fatal("private target reached upstream or lost failure state")
	}
}
