package ashby

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
	maxBodyBytes = 64 << 20
	userAgent    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
	accept       = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)

var tokenRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func TokenURL(token string) (string, error) {
	if !tokenRE.MatchString(token) {
		return "", errors.New("Ashby token is not a canonical board token")
	}
	return "https://api.ashbyhq.com/posting-api/job-board/" + token + "?includeCompensation=true", nil
}

type FetchResult struct {
	Inventory
	Status    int    `json:"status"`
	Requests  int    `json:"requests"`
	Responses int    `json:"responses"`
	Bytes     int    `json:"bytes"`
	FinalURL  string `json:"final_url,omitempty"`
	TDMPolicy string `json:"tdm_policy,omitempty"`
	Error     string `json:"error,omitempty"`
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
		return nil, fmt.Errorf("Ashby host %s has no DNS addresses", host)
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		ip := answer.IP
		if ip == nil || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() || !ip.IsGlobalUnicast() {
			return nil, errors.New("Ashby host resolved to a non-public address")
		}
	}
	for _, answer := range answers {
		connection, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port))
		if err == nil {
			return connection, nil
		}
	}
	return nil, fmt.Errorf("Ashby host %s has no reachable public address", host)
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	return &http.Client{Timeout: 30 * time.Second, Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

// Fetch makes the single request that the Python rich monitor currently owns.
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
	body, err := io.ReadAll(io.LimitReader(response.Body, maxBodyBytes+1))
	result.Bytes = len(body)
	if err != nil {
		return result, err
	}
	if len(body) > maxBodyBytes {
		return result, errors.New("Ashby response exceeded 64 MiB")
	}
	if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return result, errors.New("tdm-reservation=1")
	}
	if response.StatusCode != http.StatusOK {
		return result, fmt.Errorf("Ashby returned HTTP %d", response.StatusCode)
	}
	result.Inventory, err = Parse(body)
	return result, err
}

func FetchToken(ctx context.Context, token string) (FetchResult, error) {
	endpoint, err := TokenURL(token)
	if err != nil {
		return FetchResult{}, err
	}
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, endpoint)
}
