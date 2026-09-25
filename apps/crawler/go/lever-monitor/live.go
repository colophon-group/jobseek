package lever

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"regexp"
	"strings"
	"time"
)

const (
	maxPageBytes = 64 << 20
	userAgent    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

var tokenRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func TokenURL(token, region string, skip int) (string, error) {
	if !tokenRE.MatchString(token) || (region != "" && region != "eu") || skip < 0 || skip > MaxJobs {
		return "", errors.New("Lever token, region, or page offset is not canonical")
	}
	host := "api.lever.co"
	if region == "eu" {
		host = "api.eu.lever.co"
	}
	return fmt.Sprintf("https://%s/v0/postings/%s?limit=%d&skip=%d", host, token, BatchSize, skip), nil
}

type FetchResult struct {
	Inventory
	Status    int    `json:"status"`
	Requests  int    `json:"requests"`
	Responses int    `json:"responses"`
	Bytes     int    `json:"bytes"`
	LastSkip  int    `json:"last_skip"`
	FinalURL  string `json:"final_url,omitempty"`
	TDMPolicy string `json:"tdm_policy,omitempty"`
	Error     string `json:"error,omitempty"`
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
		return nil, fmt.Errorf("Lever host %s has no DNS addresses", host)
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		ip := answer.IP
		if ip == nil || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() || !ip.IsGlobalUnicast() {
			return nil, errors.New("Lever host resolved to a non-public address")
		}
	}
	for _, answer := range answers {
		connection, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port))
		if err == nil {
			return connection, nil
		}
	}
	return nil, fmt.Errorf("Lever host %s has no reachable public address", host)
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	return &http.Client{Timeout: 30 * time.Second, Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

func retryableStatus(status int) bool {
	return status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
}

func retryDelay(ctx context.Context, attempt int) error {
	base := time.Duration(1<<attempt) * 500 * time.Millisecond
	delay := base/2 + time.Duration(rand.Int63n(int64(base)))
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func onePage(ctx context.Context, client requestDoer, token, region string, skip int, result *FetchResult) ([]Job, int, error) {
	endpoint, err := TokenURL(token, region, skip)
	if err != nil {
		return nil, 0, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, 0, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", "application/json")
		result.Requests++
		result.LastSkip = skip
		response, err := client.Do(request)
		if err == nil {
			result.Responses++
			result.Status = response.StatusCode
			result.FinalURL = response.Request.URL.String()
			body, readErr := io.ReadAll(io.LimitReader(response.Body, maxPageBytes+1))
			response.Body.Close()
			result.Bytes += len(body)
			if readErr != nil {
				err = readErr
			} else if len(body) > maxPageBytes {
				return nil, 0, errors.New("Lever page exceeded 64 MiB")
			} else if result.Bytes > maxPageBytes {
				return nil, 0, errors.New("Lever run exceeded 64 MiB of responses")
			} else if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
				result.TDMPolicy = response.Header.Get("TDM-Policy")
				return nil, 0, errors.New("tdm-reservation=1")
			} else if response.StatusCode == http.StatusOK {
				jobs, count, parseErr := ParsePage(body)
				if parseErr == nil {
					return jobs, count, nil
				}
				err = parseErr
			} else if !retryableStatus(response.StatusCode) {
				return nil, 0, fmt.Errorf("Lever returned HTTP %d", response.StatusCode)
			} else {
				err = fmt.Errorf("Lever returned transient HTTP %d", response.StatusCode)
			}
		}
		if attempt == 2 {
			return nil, 0, err
		}
		if waitErr := retryDelay(ctx, attempt); waitErr != nil {
			return nil, 0, waitErr
		}
	}
	return nil, 0, errors.New("unreachable Lever retry state")
}

// Fetch owns an entire selected Lever pagination run. No partial inventory is
// returned to the Python persistence layer when any page fails.
func Fetch(ctx context.Context, client requestDoer, token, region string) (FetchResult, error) {
	result := FetchResult{Inventory: Inventory{Jobs: []Job{}}}
	if _, err := TokenURL(token, region, 0); err != nil {
		return result, err
	}
	for skip := 0; skip <= MaxJobs; skip += BatchSize {
		jobs, count, err := onePage(ctx, client, token, region, skip, &result)
		if err != nil {
			result.Inventory = Inventory{Jobs: []Job{}}
			return result, err
		}
		result.Jobs = append(result.Jobs, jobs...)
		if count < BatchSize {
			return result, nil
		}
		if len(result.Jobs) >= MaxJobs {
			result.Truncated = true
			return result, nil
		}
		select {
		case <-ctx.Done():
			return result, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return result, errors.New("Lever pagination exceeded the supported page bound")
}

func FetchToken(ctx context.Context, token, region string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, token, region)
}
