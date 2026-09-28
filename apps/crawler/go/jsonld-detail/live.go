package jsonld

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/netip"
	"net/url"
	"strings"
	"time"
)

const maxResponseBytes = 16 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
const accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

var nonPublicPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("192.88.99.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"),
	netip.MustParsePrefix("::/96"),
	netip.MustParsePrefix("64:ff9b::/96"),
	netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("100::/64"),
	netip.MustParsePrefix("2001::/32"),
	netip.MustParsePrefix("2001:2::/48"),
	netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("2002::/16"),
	netip.MustParsePrefix("3fff::/20"),
	netip.MustParsePrefix("fec0::/10"),
}

func publicAddress(ip netip.Addr) bool {
	if !ip.IsValid() || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() {
		return false
	}
	for _, prefix := range nonPublicPrefixes {
		if prefix.Contains(ip) {
			return false
		}
	}
	return true
}

type FetchResult struct {
	Content   map[string]any `json:"content"`
	Requests  int            `json:"requests"`
	Responses int            `json:"responses"`
	Bytes     int            `json:"bytes"`
	Status    int            `json:"status"`
	FinalURL  string         `json:"final_url,omitempty"`
	ErrorKind string         `json:"error_kind,omitempty"`
	TDMPolicy string         `json:"tdm_policy,omitempty"`
	TDMSource string         `json:"tdm_source,omitempty"`
	Error     string         `json:"error,omitempty"`
}

type requestDoer interface {
	Do(*http.Request) (*http.Response, error)
}

func publicDialContext(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return nil, err
	}
	answers, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	if len(answers) == 0 {
		return nil, errors.New("JSON-LD host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("JSON-LD host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port))
		if err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("JSON-LD host has no reachable public address")
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	// A custom DialContext disables automatic HTTP/2 setup unless forced.
	transport.ForceAttemptHTTP2 = true
	transport.ResponseHeaderTimeout = 30 * time.Second
	transport.TLSHandshakeTimeout = 30 * time.Second
	jar, _ := cookiejar.New(nil)
	return &http.Client{
		Transport:     transport,
		Jar:           jar,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func validEndpoint(endpoint string) bool {
	u, err := url.Parse(endpoint)
	return err == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Hostname() != "" && u.User == nil &&
		(u.Port() == "" || u.Scheme == "https" && u.Port() == "443" || u.Scheme == "http" && u.Port() == "80") && u.Fragment == ""
}

type readIdleBody struct {
	io.ReadCloser
	timeout time.Duration
}

func (r *readIdleBody) Read(p []byte) (int, error) {
	timer := time.AfterFunc(r.timeout, func() { _ = r.ReadCloser.Close() })
	n, err := r.ReadCloser.Read(p)
	if !timer.Stop() {
		return n, context.DeadlineExceeded
	}
	return n, err
}

// Count each redirect request and consume only one bounded response at a time.
// DNS/IP validation remains in the transport for every target.
func fetchPage(ctx context.Context, client requestDoer, endpoint string, stats *FetchResult, headers map[string]string) ([]byte, error) {
	initial, _ := url.Parse(endpoint)
	for hop := 0; hop <= 20; hop++ {
		if !validEndpoint(endpoint) {
			return nil, errors.New("JSON-LD requires a public HTTP(S) endpoint")
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", accept)
		for k, v := range headers {
			target, _ := url.Parse(endpoint)
			sameOrigin := target.Scheme == initial.Scheme && target.Host == initial.Host
			if strings.EqualFold(k, "Cookie") && hop > 0 {
				continue
			}
			if !strings.EqualFold(k, "Authorization") || sameOrigin {
				request.Header.Set(k, v)
			}
		}
		stats.Requests++
		response, err := client.Do(request)
		if err != nil {
			return nil, err
		}
		stats.Responses++
		stats.Status = response.StatusCode
		stats.FinalURL = endpoint
		if response.Request != nil && response.Request.URL != nil {
			stats.FinalURL = response.Request.URL.String()
		}
		if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
			response.Body.Close()
			stats.ErrorKind = "tdm"
			stats.TDMSource = "header"
			stats.TDMPolicy = response.Header.Get("TDM-Policy")
			return nil, errors.New("tdm-reservation=1")
		}
		body, readErr := io.ReadAll(io.LimitReader(&readIdleBody{ReadCloser: response.Body, timeout: 30 * time.Second}, maxResponseBytes+1))
		response.Body.Close()
		stats.Bytes += len(body)
		if readErr != nil {
			return nil, readErr
		}
		if len(body) > maxResponseBytes {
			return nil, errors.New("JSON-LD response exceeded 16 MiB")
		}
		body = decodedBody(body, response.Header.Get("Content-Type"))
		reservation, metaPolicy := tdmMetadata(body)
		if reservation == "1" {
			stats.ErrorKind = "tdm"
			stats.TDMSource = "meta"
			stats.TDMPolicy = response.Header.Get("TDM-Policy")
			if metaPolicy != "" {
				stats.TDMPolicy = metaPolicy
			}
			return nil, errors.New("tdm-reservation=1")
		}
		switch response.StatusCode {
		case 301, 302, 303, 307, 308:
			if response.Header.Get("Location") != "" {
				target, err := response.Location()
				if err != nil {
					return nil, err
				}
				endpoint = target.String()
				continue
			}
		}
		return body, nil
	}
	return nil, errors.New("JSON-LD exceeded 20 redirects")
}
