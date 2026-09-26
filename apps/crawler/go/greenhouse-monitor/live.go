package greenhouse

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"regexp"
	"strings"
	"time"
)

const (
	ElasticURL   = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
	MaxBodyBytes = 64 << 20
	userAgent    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
	accept       = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)

var tokenRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func TokenURL(token string) (string, error) {
	if !tokenRE.MatchString(token) {
		return "", errors.New("Greenhouse token is not a canonical board token")
	}
	return "https://boards-api.greenhouse.io/v1/boards/" + token + "/jobs?content=true", nil
}

type FetchResult struct {
	Inventory
	Status    int    `json:"status"`
	Requests  int    `json:"requests"`
	Responses int    `json:"responses"`
	Bytes     int    `json:"bytes"`
	Error     string `json:"error,omitempty"`
	TDMPolicy string `json:"tdm_policy,omitempty"`
	FinalURL  string `json:"final_url,omitempty"`
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	transport.MaxConnsPerHost = 20
	transport.MaxIdleConnsPerHost = 10
	return &http.Client{
		Timeout:   30 * time.Second,
		Transport: transport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			// A changed redirect policy needs a captured same-request replay.
			// Return the 3xx as one accounted response and fail this pilot.
			return http.ErrUseLastResponse
		},
	}
}

func publicDialContext(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return nil, err
	}
	addresses, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	public, err := publicAddresses(addresses)
	if err != nil {
		return nil, fmt.Errorf("Greenhouse host %s: %w", host, err)
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, ip := range public {
		connection, err := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
		if err == nil {
			return connection, nil
		}
	}
	return nil, fmt.Errorf("Greenhouse host %s has no public address", host)
}

func publicAddresses(addresses []net.IPAddr) ([]net.IP, error) {
	if len(addresses) == 0 {
		return nil, errors.New("has no DNS addresses")
	}
	public := make([]net.IP, 0, len(addresses))
	for _, candidate := range addresses {
		ip := candidate.IP
		if ip == nil || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() || !ip.IsGlobalUnicast() {
			return nil, errors.New("resolved to a non-public address")
		}
		public = append(public, ip)
	}
	return public, nil
}

// Fetch makes exactly one GET, matching the existing Greenhouse monitor's
// request count. Parsing completes before any rich jobs reach the writer.
func Fetch(ctx context.Context, client *http.Client, endpoint string) (FetchResult, error) {
	result := FetchResult{Requests: 1}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return result, err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("Accept", accept)
	response, err := client.Do(request)
	if err != nil {
		return result, err
	}
	defer response.Body.Close()
	result.Responses = 1
	result.Status = response.StatusCode
	result.FinalURL = response.Request.URL.String()
	body, err := io.ReadAll(io.LimitReader(response.Body, MaxBodyBytes+1))
	result.Bytes = len(body)
	if err != nil {
		return result, err
	}
	if len(body) > MaxBodyBytes {
		return result, errors.New("Greenhouse response exceeded 64 MiB")
	}
	if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return result, errors.New("tdm-reservation=1")
	}
	if response.StatusCode != http.StatusOK {
		return result, fmt.Errorf("Greenhouse returned HTTP %d", response.StatusCode)
	}
	result.Inventory, err = Parse(body)
	return result, err
}

func FetchElastic(ctx context.Context) (FetchResult, error) {
	return Fetch(ctx, newClient(), ElasticURL)
}

func FetchToken(ctx context.Context, token string) (FetchResult, error) {
	endpoint, err := TokenURL(token)
	if err != nil {
		return FetchResult{}, err
	}
	return Fetch(ctx, newClient(), endpoint)
}
