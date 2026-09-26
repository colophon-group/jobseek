package teamtailorrss

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const maxPageBytes = 32 << 20
const maxPages = 1_000
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

var nonPublicPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"), netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("192.0.0.0/24"), netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("192.88.99.0/24"), netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"), netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"), netip.MustParsePrefix("::/96"),
	netip.MustParsePrefix("64:ff9b::/96"), netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("100::/64"), netip.MustParsePrefix("2001::/32"),
	netip.MustParsePrefix("2001:2::/48"), netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("2002::/16"), netip.MustParsePrefix("3fff::/20"),
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
		return nil, errors.New("Teamtailor feed host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("Teamtailor feed host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		if conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port)); err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("Teamtailor feed host has no reachable public address")
}

func validFeedURL(raw string) (*url.URL, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed == nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.RawQuery != "" || parsed.Port() != "" && parsed.Port() != "443" || !strings.HasSuffix(parsed.Path, "/jobs.rss") {
		return nil, errors.New("Teamtailor feed URL is not canonical HTTPS /jobs.rss")
	}
	return parsed, nil
}

func pageURL(feed *url.URL, offset int) string {
	page := *feed
	query := url.Values{}
	query.Set("offset", fmt.Sprint(offset))
	query.Set("per_page", fmt.Sprint(PageSize))
	page.RawQuery = query.Encode()
	return page.String()
}

func retryable(status int) bool {
	return status == 400 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
}

func tdmReserved(value string) bool {
	value = strings.TrimSpace(value)
	if strings.ContainsAny(value, ",;") {
		return false
	}
	parsed, err := strconv.Atoi(value)
	return err == nil && parsed == 1
}

type FetchResult struct {
	Inventory
	Requests  int    `json:"requests"`
	Responses int    `json:"responses"`
	Bytes     int64  `json:"bytes"`
	Status    int    `json:"status"`
	FinalURL  string `json:"final_url,omitempty"`
	ErrorKind string `json:"error_kind,omitempty"`
	TDMPolicy string `json:"tdm_policy,omitempty"`
	Error     string `json:"error,omitempty"`
}

type requestDoer interface {
	Do(*http.Request) (*http.Response, error)
}

func fetchPage(ctx context.Context, client requestDoer, endpoint string, result *FetchResult) ([]Job, int, error) {
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			delay := time.Duration(rand.Int63n(int64(500*time.Millisecond) << (attempt - 1)))
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil, 0, ctx.Err()
			case <-timer.C:
			}
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, 0, err
		}
		request.Header.Set("User-Agent", userAgent)
		result.Requests++
		response, err := client.Do(request)
		if err != nil {
			if attempt < 2 {
				continue
			}
			return nil, 0, err
		}
		result.Responses++
		result.Status = response.StatusCode
		result.FinalURL = response.Request.URL.String()
		if tdmReserved(response.Header.Get("TDM-Reservation")) {
			result.ErrorKind = "tdm"
			result.TDMPolicy = response.Header.Get("TDM-Policy")
			response.Body.Close()
			return nil, 0, errors.New("tdm-reservation=1")
		}
		body, err := io.ReadAll(io.LimitReader(response.Body, maxPageBytes+1))
		response.Body.Close()
		result.Bytes += int64(len(body))
		if err != nil {
			return nil, 0, err
		}
		if len(body) > maxPageBytes {
			return nil, 0, errors.New("Teamtailor RSS page exceeded 32 MiB")
		}
		if response.StatusCode != 200 {
			if retryable(response.StatusCode) && attempt < 2 {
				continue
			}
			return nil, 0, fmt.Errorf("Teamtailor RSS returned HTTP %d", response.StatusCode)
		}
		return ParsePage(body)
	}
	return nil, 0, errors.New("Teamtailor RSS retry budget exhausted")
}

// Fetch follows the same 100-item offset pages and 50,000 rich-job cap as the
// existing Python Teamtailor preset. It never emits a partial successful run.
func Fetch(ctx context.Context, client requestDoer, feedURL string) (FetchResult, error) {
	result := FetchResult{Inventory: Inventory{Jobs: []Job{}}}
	feed, err := validFeedURL(feedURL)
	if err != nil {
		return result, err
	}
	for page := 0; page < maxPages; page++ {
		jobs, rawItems, err := fetchPage(ctx, client, pageURL(feed, page*PageSize), &result)
		if err != nil {
			return result, err
		}
		result.Jobs = append(result.Jobs, jobs...)
		if len(result.Jobs) >= MaxJobs {
			result.Jobs = result.Jobs[:MaxJobs]
			result.Truncated = true
			return result, nil
		}
		if rawItems < PageSize {
			return result, nil
		}
	}
	return result, errors.New("Teamtailor RSS exceeded 1000 pages")
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	// The public-address dialer is custom, so opt in to negotiated HTTP/2.
	transport.ForceAttemptHTTP2 = true
	return &http.Client{
		Timeout: 90 * time.Second, Transport: transport,
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) >= 3 {
				return errors.New("Teamtailor RSS redirected more than three times")
			}
			if _, err := validRedirectURL(request.URL); err != nil {
				return err
			}
			return nil
		},
	}
}

func validRedirectURL(parsed *url.URL) (*url.URL, error) {
	if parsed == nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.Port() != "" && parsed.Port() != "443" {
		return nil, errors.New("Teamtailor RSS redirect is not canonical HTTPS")
	}
	return parsed, nil
}

func FetchFeed(ctx context.Context, feedURL string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, feedURL)
}
