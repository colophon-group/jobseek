package join

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
	"sync"
	"time"
)

const maxResponseBytes = 8 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

var metaRE = regexp.MustCompile(`(?is)<meta\b[^>]*>`)
var tdmNameRE = regexp.MustCompile(`(?i)\bname\s*=\s*["']tdm-reservation["']`)
var tdmContentRE = regexp.MustCompile(`(?i)\bcontent\s*=\s*["']1["']`)

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

func tdmMetaReserved(body []byte) bool {
	for _, tag := range metaRE.FindAll(body[:min(len(body), 512)], -1) {
		if tdmNameRE.Match(tag) && tdmContentRE.Match(tag) {
			return true
		}
	}
	return false
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
	// Keep transport request counts aligned with explicit application attempts.
	transport.ForceAttemptHTTP2 = false
	return &http.Client{
		Timeout:       30 * time.Second,
		Transport:     transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
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
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			result.err = err
			return result
		}
		request.Header.Set("User-Agent", userAgent)
		result.stats.Requests++
		response, err := client.Do(request)
		if err == nil {
			result.stats.Responses++
			result.stats.Status = response.StatusCode
			result.stats.FinalURL = response.Request.URL.String()
			if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
				response.Body.Close()
				result.stats.ErrorKind = "tdm"
				result.stats.TDMPolicy = response.Header.Get("TDM-Policy")
				result.err = errors.New("tdm-reservation=1")
				return result
			}
			body, readErr := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
			response.Body.Close()
			result.stats.Bytes += len(body)
			if readErr != nil {
				err = readErr
			} else if len(body) > maxResponseBytes {
				result.err = errors.New("JOIN response exceeded 8 MiB")
				return result
			} else if tdmMetaReserved(body) {
				result.stats.ErrorKind = "tdm"
				result.stats.TDMPolicy = response.Header.Get("TDM-Policy")
				result.err = errors.New("tdm-reservation=1")
				return result
			} else if number == 1 && (response.StatusCode == 404 || response.StatusCode == 410) {
				result.stats.ErrorKind = "gone"
				result.err = fmt.Errorf("JOIN board returned HTTP %d", response.StatusCode)
				return result
			} else if response.StatusCode == 200 {
				page, parseErr := ParsePage(body, slug, number == 1)
				if parseErr == nil {
					result.page = page
					return result
				}
				err = parseErr
			} else {
				err = fmt.Errorf("JOIN returned HTTP %d", response.StatusCode)
			}
		}
		result.err = err
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
			}
			if outcome.err != nil && firstError == nil {
				firstError = outcome.err
			}
			pages = append(pages, outcome.page)
		}
		if result.Bytes > 64<<20 {
			return result, errors.New("JOIN run exceeded 64 MiB of responses")
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
