package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/base64"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

// Browser requests keep their original URL, Host and TLS identity. Only the
// CONNECT authority sent to the configured proxy uses the validated numeric
// address. The upstream proxy never receives a hostname to resolve again.
type pinnedBrowserProxyTunnel struct {
	endpoint browserproxy.Endpoint
	prefixes []netip.Prefix
	lookup   func(context.Context, string, string) ([]netip.Addr, error)
	dial     func(context.Context, string, string) (net.Conn, error)
	proxyTLS *tls.Config
}

type browserProxyConnectStatus struct{ status int }

func (e *browserProxyConnectStatus) Error() string { return "configured browser proxy tunnel rejected" }

func newPinnedBrowserProxyTransport(endpoint browserproxy.Endpoint, policy EgressPolicy) (*http.Transport, error) {
	if !browserproxy.ValidReference(endpoint.Reference()) || policy.validate() != nil {
		return nil, errBrowserProxy
	}
	var prefixes []netip.Prefix
	for _, raw := range strings.Split(policy.blockCIDRs, ",") {
		prefix, err := netip.ParsePrefix(raw)
		if err != nil {
			return nil, errBrowserProxy
		}
		prefixes = append(prefixes, prefix)
	}
	tunnel := &pinnedBrowserProxyTunnel{endpoint: endpoint, prefixes: prefixes, lookup: net.DefaultResolver.LookupNetIP, dial: (&net.Dialer{Timeout: 20 * time.Second}).DialContext, proxyTLS: &tls.Config{MinVersion: tls.VersionTLS12}}
	return &http.Transport{Proxy: nil, DialContext: tunnel.connect, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}, TLSHandshakeTimeout: 20 * time.Second, ResponseHeaderTimeout: 20 * time.Second, MaxResponseHeaderBytes: 64 << 10, DisableKeepAlives: true, MaxConnsPerHost: 4}, nil
}

func (t *pinnedBrowserProxyTunnel) connect(ctx context.Context, network, address string) (net.Conn, error) {
	if t == nil || t.lookup == nil || t.dial == nil || network != "tcp" {
		return nil, errBrowserProxy
	}
	host, port, err := net.SplitHostPort(address)
	if err != nil || host == "" || port == "" {
		return nil, errBrowserProxy
	}
	number, err := strconv.Atoi(port)
	if err != nil || number < 1 || number > 65535 || strconv.Itoa(number) != port {
		return nil, errBrowserProxy
	}
	targets, err := t.lookup(ctx, "ip", host)
	if err != nil || !replayHTTPAddressesAllowed(t.prefixes, targets) {
		return nil, errBrowserProxy
	}
	proxy, err := url.Parse(t.endpoint.Address())
	if err != nil || proxy.Hostname() == "" {
		return nil, errBrowserProxy
	}
	hops, err := t.lookup(ctx, "ip", proxy.Hostname())
	if err != nil || !replayHTTPAddressesAllowed(t.prefixes, hops) {
		return nil, errBrowserProxy
	}
	hopPort := proxy.Port()
	if hopPort == "" {
		if proxy.Scheme == "https" {
			hopPort = "443"
		} else {
			hopPort = "80"
		}
	}
	// A single checked target is pinned for this request. Endpoint rotation and
	// retries remain with the existing typed caller and its bounded attempt budget.
	var conn net.Conn
	for _, hop := range hops {
		conn, err = t.dial(ctx, "tcp", net.JoinHostPort(hop.Unmap().String(), hopPort))
		if err == nil {
			break
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
	}
	if err != nil || conn == nil {
		return nil, errBrowserProxy
	}
	accepted := false
	defer func() {
		if !accepted {
			conn.Close()
		}
	}()
	rawConn := conn
	stop := context.AfterFunc(ctx, func() { rawConn.Close() })
	defer stop()
	deadline := time.Now().Add(20 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if conn.SetDeadline(deadline) != nil {
		return nil, errBrowserProxy
	}
	if proxy.Scheme == "https" {
		if t.proxyTLS == nil {
			return nil, errBrowserProxy
		}
		config := t.proxyTLS.Clone()
		config.ServerName = proxy.Hostname()
		secured := tls.Client(conn, config)
		if secured.HandshakeContext(ctx) != nil {
			return nil, errBrowserProxy
		}
		conn = secured
	}
	authority := net.JoinHostPort(targets[0].Unmap().String(), port)
	request := &http.Request{Method: http.MethodConnect, URL: &url.URL{Opaque: authority}, Host: authority, Header: make(http.Header)}
	username, password := t.endpoint.Credentials()
	if username != "" {
		request.Header.Set("Proxy-Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(username+":"+password)))
	}
	if request.Write(conn) != nil {
		return nil, errBrowserProxy
	}
	reader := bufio.NewReader(io.LimitReader(conn, 64<<10))
	response, err := http.ReadResponse(reader, request)
	if err != nil {
		return nil, errBrowserProxy
	}
	if response.StatusCode != http.StatusOK {
		return nil, &browserProxyConnectStatus{status: response.StatusCode}
	}
	// No origin request has been sent yet. Extra proxy bytes cannot be accepted
	// as trusted tunnel data or left buffered outside net/http's TLS handshake.
	if reader.Buffered() != 0 || ctx.Err() != nil {
		return nil, errBrowserProxy
	}
	if conn.SetDeadline(time.Time{}) != nil {
		return nil, errBrowserProxy
	}
	accepted = true
	return conn, nil
}
