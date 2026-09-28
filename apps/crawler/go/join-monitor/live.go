package join

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/netip"
	"net/url"
	"strings"
	"sync"
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
	URLs      []string `json:"urls"`
	Requests  int      `json:"requests"`
	Responses int      `json:"responses"`
	Bytes     int      `json:"bytes"`
	Status    int      `json:"status"`
	FinalURL  string   `json:"final_url,omitempty"`
	ErrorKind string   `json:"error_kind,omitempty"`
	TDMPolicy string   `json:"tdm_policy,omitempty"`
	TDMSource string   `json:"tdm_source,omitempty"`
	Error     string   `json:"error,omitempty"`
}

type requestDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type pageOutcome struct {
	page  Page
	stats FetchResult
	err   error
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
		return nil, errors.New("JOIN host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("JOIN host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port))
		if err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("JOIN host has no reachable public address")
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
	return err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil &&
		(u.Port() == "" || u.Port() == "443") && u.Fragment == ""
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
func fetchPage(ctx context.Context, client requestDoer, endpoint string, stats *FetchResult) ([]byte, error) {
	for hop := 0; hop <= 20; hop++ {
		if !validEndpoint(endpoint) {
			return nil, errors.New("JOIN requires a public HTTPS endpoint")
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", accept)
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
			return nil, errors.New("JOIN response exceeded 16 MiB")
		}
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
	return nil, errors.New("JOIN exceeded 20 redirects")
}

func onePage(ctx context.Context, client requestDoer, boardURL, slug string, number int) pageOutcome {
	endpoint := boardURL
	if number > 1 {
		var err error
		endpoint, err = PageURL(boardURL, number)
		if err != nil {
			return pageOutcome{err: err}
		}
	}
	result := pageOutcome{}
	for attempt := 0; attempt < 3; attempt++ {
		body, err := fetchPage(ctx, client, endpoint, &result.stats)
		if err == nil {
			if number == 1 && (result.stats.Status == 404 || result.stats.Status == 410) {
				result.stats.ErrorKind = "gone"
				result.err = fmt.Errorf("JOIN board returned HTTP %d", result.stats.Status)
				return result
			} else if result.stats.Status == 200 {
				page, parseErr := ParsePage(body, slug, number == 1)
				if parseErr == nil {
					captureText("/tmp", endpoint, body, false)
					result.page = page
					return result
				}
				err = parseErr
			} else {
				err = fmt.Errorf("JOIN returned HTTP %d", result.stats.Status)
			}
		}
		result.err = err
		if result.stats.ErrorKind == "tdm" || ctx.Err() != nil {
			return result
		}
		if attempt == 2 {
			return result
		}
		wait := time.NewTimer(time.Duration(1<<attempt) * 500 * time.Millisecond)
		select {
		case <-ctx.Done():
			wait.Stop()
			result.err = ctx.Err()
			return result
		case <-wait.C:
		}
	}
	return result
}

// Fetch owns every request of a selected JOIN inventory. A failed required
// page returns no URLs, so the Python writer cannot delist unseen pages.
func Fetch(ctx context.Context, client requestDoer, boardURL, slug string) (FetchResult, error) {
	result := FetchResult{URLs: []string{}}
	if err := ValidateBoard(boardURL, slug); err != nil {
		return result, err
	}
	first := onePage(ctx, client, boardURL, slug, 1)
	result.Requests += first.stats.Requests
	result.Responses += first.stats.Responses
	result.Bytes += first.stats.Bytes
	result.Status = first.stats.Status
	result.FinalURL = first.stats.FinalURL
	result.ErrorKind = first.stats.ErrorKind
	result.TDMPolicy = first.stats.TDMPolicy
	result.TDMSource = first.stats.TDMSource
	if first.err != nil {
		return result, first.err
	}
	count := first.page.PageCount
	if count <= 1 {
		urls, err := UniqueSorted([]Page{first.page})
		if err != nil {
			return result, err
		}
		result.URLs = urls
		return result, nil
	}
	pages := []Page{first.page}
	for start := 2; start <= count; start += 10 {
		end := min(start+9, count)
		outcomes := make([]pageOutcome, end-start+1)
		jobs := make(chan int)
		var workers sync.WaitGroup
		workerCount := min(len(outcomes), 5)
		workers.Add(workerCount)
		for range workerCount {
			go func() {
				defer workers.Done()
				for number := range jobs {
					outcomes[number-start] = onePage(ctx, client, boardURL, slug, number)
				}
			}()
		}
		for number := start; number <= end; number++ {
			jobs <- number
		}
		close(jobs)
		workers.Wait()
		var firstError error
		for _, outcome := range outcomes {
			result.Requests += outcome.stats.Requests
			result.Responses += outcome.stats.Responses
			result.Bytes += outcome.stats.Bytes
			if outcome.stats.Status != 0 {
				result.Status = outcome.stats.Status
				result.FinalURL = outcome.stats.FinalURL
			}
			if outcome.stats.ErrorKind != "" && result.ErrorKind == "" {
				result.ErrorKind = outcome.stats.ErrorKind
				result.TDMPolicy = outcome.stats.TDMPolicy
				result.TDMSource = outcome.stats.TDMSource
			}
			if outcome.err != nil && firstError == nil {
				firstError = outcome.err
			}
			pages = append(pages, outcome.page)
		}
		if firstError != nil {
			return result, firstError
		}
	}
	urls, err := UniqueSorted(pages)
	if err != nil {
		return result, err
	}
	result.URLs = urls
	return result, nil
}

func FetchBoard(ctx context.Context, boardURL, slug string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, boardURL, slug)
}
