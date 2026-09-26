package recruitee

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"regexp"
	"strings"
	"time"
)

const maxResponseBytes = 16 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

var tenantRE = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var metaRE = regexp.MustCompile(`(?is)<meta\b[^>]*>`)
var tdmNameRE = regexp.MustCompile(`(?i)\bname\s*=\s*["']?tdm-reservation(?:["']|\s|/?>)`)
var tdmContentRE = regexp.MustCompile(`(?i)\bcontent\s*=\s*["']?1(?:["']|\s|/?>)`)

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
		return nil, errors.New("Recruitee host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("Recruitee host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port))
		if err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("Recruitee host has no reachable public address")
}

func tdmMetaReserved(body []byte) bool {
	for _, tag := range metaRE.FindAll(body, -1) {
		if tdmNameRE.Match(tag) && tdmContentRE.Match(tag) {
			return true
		}
	}
	return false
}

type FetchResult struct {
	Inventory
	Requests  int    `json:"requests"`
	Responses int    `json:"responses"`
	Bytes     int    `json:"bytes"`
	Status    int    `json:"status"`
	FinalURL  string `json:"final_url,omitempty"`
	ErrorKind string `json:"error_kind,omitempty"`
	TDMPolicy string `json:"tdm_policy,omitempty"`
	Error     string `json:"error,omitempty"`
}

type requestDoer interface {
	Do(*http.Request) (*http.Response, error)
}

func Fetch(ctx context.Context, client requestDoer, tenant string) (FetchResult, error) {
	result := FetchResult{Inventory: Inventory{Jobs: []Job{}}}
	if !tenantRE.MatchString(tenant) {
		return result, errors.New("Recruitee tenant is not canonical")
	}
	endpoint := "https://" + tenant + ".recruitee.com/api/offers"
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return result, err
	}
	request.Header.Set("User-Agent", userAgent)
	result.Requests = 1
	response, err := client.Do(request)
	if err != nil {
		return result, err
	}
	defer response.Body.Close()
	result.Responses = 1
	result.Status = response.StatusCode
	result.FinalURL = response.Request.URL.String()
	if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
		result.ErrorKind = "tdm"
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return result, errors.New("tdm-reservation=1")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
	result.Bytes = len(body)
	if err != nil {
		return result, err
	}
	if len(body) > maxResponseBytes {
		return result, errors.New("Recruitee response exceeded 16 MiB")
	}
	if tdmMetaReserved(body) {
		result.ErrorKind = "tdm"
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return result, errors.New("tdm-reservation=1")
	}
	if response.StatusCode == 404 {
		result.ErrorKind = "gone"
		return result, errors.New("Recruitee tenant returned HTTP 404")
	}
	if response.StatusCode != 200 {
		return result, fmt.Errorf("Recruitee returned HTTP %d", response.StatusCode)
	}
	inventory, err := Parse(body)
	if err != nil {
		return result, err
	}
	result.Inventory = inventory
	return result, nil
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	// A custom DialContext disables automatic HTTP/2 setup unless forced.
	transport.ForceAttemptHTTP2 = true
	return &http.Client{
		Timeout: 30 * time.Second, Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func FetchTenant(ctx context.Context, tenant string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, tenant)
}
