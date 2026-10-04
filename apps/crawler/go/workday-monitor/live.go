package workday

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// The headers and retry budget match the current Python Workday list path.
const (
	userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
	accept    = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
	attempts  = 3
)

// FetchError preserves the last upstream status for the Python worker's
// existing provider-incident and host-circuit accounting.
type FetchError struct {
	URL      string
	Attempts int
	Status   int
	Kind     string
	Cause    error
}

func (e *FetchError) Error() string {
	if e.Status != 0 {
		return fmt.Sprintf("Workday list fetch failed for %s after %d attempts (status=%d)", e.URL, e.Attempts, e.Status)
	}
	return fmt.Sprintf("Workday list fetch failed for %s after %d attempts: %v", e.URL, e.Attempts, e.Cause)
}

func (e *FetchError) Unwrap() error { return e.Cause }

type ReservationError struct {
	URL       string
	PolicyURL string
}

func (e *ReservationError) Error() string {
	return fmt.Sprintf("tdm-reservation=1 declared by %s", e.URL)
}

// LivePoster owns the HTTP connections for one configured Workday site.
// A Python worker may hand off that site's list phase, but not its scheduler,
// persistence, or detail scraping. Only the exact list endpoint is allowed.
type LivePoster struct {
	mu              sync.Mutex
	client          *http.Client
	listURL         string
	tenant          *Site
	allowedSites    map[string]bool
	sleep           func(context.Context, time.Duration) error
	random          func() float64
	Requests        int
	Responses       int
	TransportErrors int
	Bytes           int64
}

// NewInventoryPoster reuses an already verified caller-owned HTTP transport for
// all configured Workday sites. Redirects remain disabled so a provider 303
// cannot silently convert the original list POST into a GET.
func NewInventoryPoster(c InventoryConfig, client *http.Client) (*LivePoster, error) {
	if err := c.validate(); err != nil {
		return nil, err
	}
	if client == nil {
		return nil, errors.New("verified HTTP client required")
	}
	copyClient := *client
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	site := c.Site
	p := &LivePoster{client: &copyClient, tenant: &site, sleep: sleepContext, random: rand.Float64}
	if len(c.Sites) > 0 || !c.AllSites {
		p.allowedSites = map[string]bool{}
		if len(c.Sites) == 0 {
			p.allowedSites[site.Name] = true
		} else {
			for _, name := range c.Sites {
				p.allowedSites[name] = true
			}
		}
	}
	return p, nil
}

func (p *LivePoster) acceptsList(rawURL string) bool {
	if p.tenant == nil {
		return rawURL == p.listURL
	}
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "https" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || u.Host != p.tenant.Company+"."+p.tenant.Instance+".myworkdayjobs.com" {
		return false
	}
	parts := strings.Split(strings.TrimPrefix(u.Path, "/"), "/")
	return len(parts) == 5 && parts[0] == "wday" && parts[1] == "cxs" && parts[2] == p.tenant.Company && siteToken.MatchString(parts[3]) && parts[4] == "jobs" && (p.allowedSites == nil || p.allowedSites[parts[3]])
}

func (p *LivePoster) count(requests, responses, transportErrors int, bytes int64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Requests += requests
	p.Responses += responses
	p.TransportErrors += transportErrors
	p.Bytes += bytes
}

// GetRobots preserves fallback for unavailable robots documents while retaining
// cancellation and publisher reservations as errors to stop inventory writes.
func (p *LivePoster) GetRobots(ctx context.Context, rawURL string) (string, error) {
	if p.tenant == nil || rawURL != "https://"+p.tenant.Company+"."+p.tenant.Instance+".myworkdayjobs.com/robots.txt" {
		return "", errors.New("invalid Workday robots endpoint")
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return "", err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("Accept", accept)
	p.count(1, 0, 0, 0)
	response, err := p.client.Do(request)
	if err != nil {
		p.count(0, 0, 1, 0)
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", nil
	}
	defer response.Body.Close()
	p.count(0, 1, 0, 0)
	reserved, parseErr := strconv.Atoi(strings.TrimSpace(response.Header.Get("TDM-Reservation")))
	if parseErr == nil && reserved == 1 {
		return "", &ReservationError{URL: rawURL, PolicyURL: response.Header.Get("TDM-Policy")}
	}
	if response.StatusCode != http.StatusOK {
		return "", nil
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20+1))
	p.count(0, 0, 0, int64(len(body)))
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", nil
	}
	if len(body) > 1<<20 {
		return "", errors.New("Workday robots document oversized")
	}
	return string(body), nil
}

func NewLivePoster(site Site) (*LivePoster, error) {
	if !companyToken.MatchString(site.Company) || !instanceToken.MatchString(site.Instance) || !siteToken.MatchString(site.Name) {
		return nil, errors.New("valid Workday site is required")
	}
	host := site.Company + "." + site.Instance + ".myworkdayjobs.com"
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	// Some Workday edges negotiate HTTP/2. With the guarded custom dialer,
	// Go otherwise sends HTTP/1.1 and misreads HTTP/2 frames as a response.
	transport.ForceAttemptHTTP2 = true
	transport.MaxConnsPerHost = 20
	transport.MaxIdleConnsPerHost = 10
	return &LivePoster{
		client: &http.Client{
			Timeout:   30 * time.Second,
			Transport: transport,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
		listURL: "https://" + host + "/wday/cxs/" + site.Company + "/" + site.Name + "/jobs",
		sleep:   sleepContext,
		random:  rand.Float64,
	}, nil
}

// publicDialContext rejects private DNS answers, as the Python crawler's
// SSRF-guarded transport does. The request URL itself is site-pinned above.
func publicDialContext(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return nil, err
	}
	addresses, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, address := range addresses {
		ip := address.IP
		if ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() || !ip.IsGlobalUnicast() {
			continue
		}
		connection, err := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
		if err == nil {
			return connection, nil
		}
	}
	return nil, fmt.Errorf("Workday host %s has no public address", host)
}

func sleepContext(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func retryable(status int) bool {
	return status == 303 || status == 408 || status == 425 || status == 429 || status >= 500 && status < 600
}

func (p *LivePoster) Post(ctx context.Context, rawURL string, body []byte) ([]byte, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || !p.acceptsList(rawURL) || parsed.Scheme != "https" || parsed.User != nil {
		return nil, errors.New("Workday request differs from the configured list endpoint")
	}
	var lastStatus int
	var lastError error
	var lastKind string
	for attempt := 0; attempt < attempts; attempt++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, rawURL, bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", accept)
		request.Header.Set("Content-Type", "application/json")
		p.count(1, 0, 0, 0)
		response, err := p.client.Do(request)
		if err == nil {
			p.count(0, 1, 0, 0)
			lastStatus = response.StatusCode
			reservation := strings.TrimSpace(response.Header.Get("TDM-Reservation"))
			reserved, parseErr := strconv.Atoi(reservation)
			if response.StatusCode == http.StatusOK && parseErr == nil && reserved == 1 {
				content, _ := io.ReadAll(response.Body)
				p.count(0, 0, 0, int64(len(content)))
				response.Body.Close()
				return nil, &ReservationError{URL: rawURL, PolicyURL: response.Header.Get("TDM-Policy")}
			}
			content, readErr := io.ReadAll(response.Body)
			response.Body.Close()
			p.count(0, 0, 0, int64(len(content)))
			if readErr != nil {
				lastStatus = 0
				lastError = readErr
				lastKind = "transport"
			} else if response.StatusCode == http.StatusOK {
				var page map[string]json.RawMessage
				if json.Unmarshal(content, &page) == nil && page != nil {
					return content, nil
				}
				lastStatus = 0
				lastError = errors.New("invalid Workday JSON object")
				lastKind = "decode"
			} else {
				if !retryable(response.StatusCode) {
					return nil, &FetchError{URL: rawURL, Attempts: attempt + 1, Status: response.StatusCode}
				}
				lastError = nil
				lastKind = ""
			}
		} else {
			p.count(0, 0, 1, 0)
			lastStatus = 0
			lastError = err
			lastKind = "transport"
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if attempt < attempts-1 {
			delay := time.Duration(float64(time.Second<<attempt) * (0.5 + p.random()))
			if err := p.sleep(ctx, delay); err != nil {
				return nil, err
			}
		}
	}
	return nil, &FetchError{URL: rawURL, Attempts: attempts, Status: lastStatus, Kind: lastKind, Cause: lastError}
}
