package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

func testPinnedTunnel(t *testing.T, serve func(net.Conn)) (*http.Transport, <-chan string) {
	t.Helper()
	endpoint, err := browserproxy.Parse("http://fixture-user:fixture-password@proxy.example:8080")
	if err != nil {
		t.Fatal(err)
	}
	var prefixes []netip.Prefix
	for _, raw := range strings.Split(defaultEgressPolicy().blockCIDRs, ",") {
		prefixes = append(prefixes, netip.MustParsePrefix(raw))
	}
	seen := make(chan string, 1)
	tunnel := &pinnedBrowserProxyTunnel{endpoint: endpoint, prefixes: prefixes, proxyTLS: &tls.Config{MinVersion: tls.VersionTLS12}}
	tunnel.lookup = func(ctx context.Context, network, host string) ([]netip.Addr, error) {
		if host == "proxy.example" {
			return []netip.Addr{netip.MustParseAddr("93.184.216.35")}, nil
		}
		return []netip.Addr{netip.MustParseAddr("93.184.216.34")}, nil
	}
	tunnel.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "93.184.216.35:8080" {
			return nil, fmt.Errorf("upstream dial was not pinned")
		}
		client, server := net.Pipe()
		t.Cleanup(func() { client.Close(); server.Close() })
		go func() {
			defer server.Close()
			server.SetDeadline(time.Now().Add(3 * time.Second))
			reader := bufio.NewReader(server)
			request, err := http.ReadRequest(reader)
			if err != nil {
				seen <- "invalid CONNECT"
				return
			}
			expected := "Basic " + base64.StdEncoding.EncodeToString([]byte("fixture-user:fixture-password"))
			if request.Method != "CONNECT" || request.Host != "93.184.216.34:443" || request.Header.Get("Proxy-Authorization") != expected {
				seen <- "authority or proxy auth differs"
				return
			}
			seen <- request.Host
			if _, err := io.WriteString(server, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
				return
			}
			serve(server)
		}()
		return client, nil
	}
	transport := &http.Transport{DialContext: tunnel.connect, DisableKeepAlives: true, TLSHandshakeTimeout: time.Second}
	t.Cleanup(transport.CloseIdleConnections)
	return transport, seen
}

func TestPinnedBrowserProxyPreservesHostAndNeverForwardsProxyCredentials(t *testing.T) {
	result := make(chan *http.Request, 1)
	transport, seen := testPinnedTunnel(t, func(conn net.Conn) {
		request, err := http.ReadRequest(bufio.NewReader(conn))
		if err != nil {
			return
		}
		result <- request
		io.WriteString(conn, "HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\ncontent")
	})
	client := &http.Client{Transport: transport, Timeout: 3 * time.Second}
	request, _ := http.NewRequest("GET", "http://origin.example:443/job", nil)
	request.Header.Set("Authorization", "Bearer origin-fixture")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || string(body) != "content" {
		t.Fatal("response content differs")
	}
	if got := <-seen; got != "93.184.216.34:443" {
		t.Fatal(got)
	}
	origin := <-result
	if origin.Host != "origin.example:443" || origin.URL.RequestURI() != "/job" || origin.Header.Get("Authorization") != "Bearer origin-fixture" || origin.Header.Get("Proxy-Authorization") != "" {
		t.Fatal("origin request identity or credential boundary differs")
	}
}

func TestPinnedBrowserProxyRetainsOriginalTLSIdentity(t *testing.T) {
	fixture := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer fixture.Close()
	certificate, err := x509.ParseCertificate(fixture.TLS.Certificates[0].Certificate[0])
	if err != nil || len(certificate.DNSNames) == 0 {
		t.Fatal("TLS fixture identity missing")
	}
	name := certificate.DNSNames[0]
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
		if _, err := http.ReadRequest(bufio.NewReader(secured)); err != nil {
			return
		}
		io.WriteString(secured, "HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\ntls")
	})
	transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	client := &http.Client{Transport: transport, Timeout: 3 * time.Second}
	response, err := client.Get("https://" + name + "/job")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || string(body) != "tls" {
		t.Fatal("TLS response differs")
	}
	if <-seen != "93.184.216.34:443" || <-seenName != name {
		t.Fatal("original certificate/SNI identity lost")
	}
}

func TestPinnedBrowserProxyRejectsBlockedAnswersBeforeAnyDial(t *testing.T) {
	endpoint, _ := browserproxy.Parse("http://proxy.example:8080")
	for _, answer := range []string{"127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"} {
		for _, blockedHop := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%v", answer, blockedHop), func(t *testing.T) {
				var prefixes []netip.Prefix
				for _, raw := range strings.Split(defaultEgressPolicy().blockCIDRs, ",") {
					prefixes = append(prefixes, netip.MustParsePrefix(raw))
				}
				tunnel := &pinnedBrowserProxyTunnel{endpoint: endpoint, prefixes: prefixes, lookup: func(ctx context.Context, network, host string) ([]netip.Addr, error) {
					if (host == "proxy.example") == blockedHop {
						return []netip.Addr{netip.MustParseAddr("93.184.216.34"), netip.MustParseAddr(answer)}, nil
					}
					return []netip.Addr{netip.MustParseAddr("93.184.216.35")}, nil
				}, dial: func(context.Context, string, string) (net.Conn, error) {
					t.Error("blocked destination reached dial")
					return nil, errors.New("unexpected dial")
				}}
				if conn, err := tunnel.connect(context.Background(), "tcp", "origin.example:443"); err == nil || conn != nil {
					t.Fatal("mixed private/public DNS answer accepted")
				}
			})
		}
	}
}
