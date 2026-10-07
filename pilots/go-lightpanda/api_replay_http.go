package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"io"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Constructed from trusted renderer startup policy, never request metadata.
// Resolve and pin every dial destination under the same deny inventory as the
// browser. HTTP trust comes from the renderer image's CA store; request data
// cannot select roots or disable verification.
func newReplayHTTPFallback(o api.BrowserReplayOptions, policy EgressPolicy) (api.Fetch, func(), error) {
	if policy.validate() != nil {
		return nil, nil, errReplayCapture
	}
	roots, err := x509.SystemCertPool()
	if err != nil {
		return nil, nil, errReplayCapture
	}
	prefixes := []netip.Prefix{}
	for _, entry := range strings.Split(policy.blockCIDRs, ",") {
		prefix, err := netip.ParsePrefix(entry)
		if err != nil {
			return nil, nil, errReplayCapture
		}
		prefixes = append(prefixes, prefix)
	}
	transport := &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}, MaxConnsPerHost: 1, MaxIdleConns: 1, MaxIdleConnsPerHost: 1, IdleConnTimeout: 30 * time.Second, TLSHandshakeTimeout: 20 * time.Second, ResponseHeaderTimeout: 20 * time.Second}
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, errReplayCapture
		}
		ips, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
		if err != nil || len(ips) == 0 {
			return nil, errReplayCapture
		}
		if !replayHTTPAddressesAllowed(prefixes, ips) {
			return nil, errReplayCapture
		}
		for _, ip := range ips {
			conn, err := (&net.Dialer{Timeout: 20 * time.Second}).DialContext(ctx, network, net.JoinHostPort(ip.Unmap().String(), port))
			if err == nil {
				return conn, nil
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
		}
		return nil, errReplayCapture
	}
	client := &http.Client{Transport: transport, Timeout: 30 * time.Second, CheckRedirect: func(next *http.Request, prior []*http.Request) error {
		if len(prior) > 20 || !o.Inventory.ResourceMatches(next.URL.String()) {
			return errReplayCapture
		}
		return nil
	}}
	return replayHTTPFetcher(o, client), transport.CloseIdleConnections, nil
}

func replayHTTPAddressesAllowed(prefixes []netip.Prefix, ips []netip.Addr) bool {
	if len(ips) == 0 || len(ips) > 128 {
		return false
	}
	for _, ip := range ips {
		if !ip.IsValid() || ip.Zone() != "" {
			return false
		}
		ip = ip.Unmap()
		for _, prefix := range prefixes {
			if prefix.Contains(ip) {
				return false
			}
		}
	}
	return true
}

// Private credentials remain local on the HTTP path too. Both response paths
// use identical resource binding, policy, reflection and JSON checks.
func replayHTTPFetcher(o api.BrowserReplayOptions, client *http.Client) api.Fetch {
	return func(ctx context.Context, r api.Request) (*api.Document, error) {
		if client == nil || r.Method != o.Inventory.Method || !o.Inventory.ResourceMatches(r.URL) || len(r.Body) > 64<<10 {
			return nil, errReplayCapture
		}
		request, err := http.NewRequestWithContext(ctx, r.Method, r.URL, strings.NewReader(r.Body))
		if err != nil {
			return nil, errReplayCapture
		}
		request.Header = replayHeaders(r.Headers)
		response, err := client.Do(request)
		if ctx.Err() != nil {
			if response != nil {
				response.Body.Close()
			}
			return nil, ctx.Err()
		}
		if err != nil || response == nil {
			return nil, errReplayCapture
		}
		defer response.Body.Close()
		body, err := io.ReadAll(io.LimitReader(response.Body, replayCaptureBodyLimit+1))
		if err != nil {
			return nil, errReplayCapture
		}
		if len(body) > replayCaptureBodyLimit {
			return nil, errResourceLimit
		}
		var reservation, policyURL *string
		if _, ok := response.Header["Tdm-Reservation"]; ok {
			value := response.Header.Get("Tdm-Reservation")
			reservation = &value
		}
		if _, ok := response.Header["Tdm-Policy"]; ok {
			value := response.Header.Get("Tdm-Policy")
			policyURL = &value
		}
		return replayResponseDocument(o, r, replayFetchResponse{Status: response.StatusCode, URL: response.Request.URL.String(), Body: string(body), Reservation: reservation, Policy: policyURL})
	}
}
