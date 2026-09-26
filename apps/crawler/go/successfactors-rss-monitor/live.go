package successfactorsrss

import (
	"bufio"
	"bytes"
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

const maxFeedBytes = 256 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

type Summary struct {
	Type      string `json:"type"`
	Items     int    `json:"items"`
	Jobs      int    `json:"jobs"`
	Truncated bool   `json:"truncated"`
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
		return nil, errors.New("SuccessFactors feed host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("SuccessFactors feed host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		if conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port)); err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("SuccessFactors feed host has no reachable public address")
}

func validFeedURL(raw string) (*url.URL, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed == nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.RawQuery != "" || parsed.Port() != "" && parsed.Port() != "443" || !strings.EqualFold(strings.TrimRight(parsed.Path, "/"), "/googlefeed.xml") {
		return nil, errors.New("SuccessFactors feed URL is not canonical HTTPS /googlefeed.xml")
	}
	return parsed, nil
}

func validRedirectURL(parsed *url.URL) error {
	if parsed == nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.Port() != "" && parsed.Port() != "443" {
		return errors.New("SuccessFactors redirect is not canonical HTTPS")
	}
	return nil
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	return &http.Client{
		Timeout: 5 * time.Minute, Transport: transport,
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) >= 3 {
				return errors.New("SuccessFactors feed redirected more than three times")
			}
			return validRedirectURL(request.URL)
		},
	}
}

func retryable(status int) bool {
	return status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
}

func tdmReserved(value string) bool {
	value = strings.TrimSpace(value)
	if strings.ContainsAny(value, ",;") {
		return false
	}
	parsed, err := strconv.Atoi(value)
	return err == nil && parsed == 1
}

type countedReader struct {
	source io.Reader
	n      int64
}

func (r *countedReader) Read(buf []byte) (int, error) {
	n, err := r.source.Read(buf)
	r.n += int64(n)
	return n, err
}

func xmlHead(reader *bufio.Reader) bool {
	head, _ := reader.Peek(512)
	head = bytes.TrimSpace(bytes.TrimPrefix(head, []byte{0xef, 0xbb, 0xbf}))
	head = bytes.ToLower(head)
	return bytes.HasPrefix(head, []byte("<?xml")) || bytes.HasPrefix(head, []byte("<rss")) || bytes.HasPrefix(head, []byte("<feed"))
}

// Fetch streams the actual response through the XML parser. An error after
// emit has run must fail the cycle; callers must never treat the partial list
// as an authoritative empty-state or deletion signal.
func Fetch(ctx context.Context, client requestDoer, feedURL string, emit func(Job) error) (Summary, error) {
	result := Summary{Type: "summary"}
	if _, err := validFeedURL(feedURL); err != nil {
		return result, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			delay := time.Duration(rand.Int63n(int64(500*time.Millisecond) << (attempt - 1)))
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return result, ctx.Err()
			case <-timer.C:
			}
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, feedURL, nil)
		if err != nil {
			return result, err
		}
		request.Header.Set("User-Agent", userAgent)
		result.Requests++
		response, err := client.Do(request)
		if err != nil {
			if attempt < 2 {
				continue
			}
			return result, err
		}
		result.Responses++
		result.Status = response.StatusCode
		if response.Request != nil && response.Request.URL != nil {
			result.FinalURL = response.Request.URL.String()
		}
		if tdmReserved(response.Header.Get("TDM-Reservation")) {
			result.ErrorKind = "tdm"
			result.TDMPolicy = response.Header.Get("TDM-Policy")
			response.Body.Close()
			return result, errors.New("tdm-reservation=1")
		}
		if response.StatusCode != 200 {
			response.Body.Close()
			if retryable(response.StatusCode) && attempt < 2 {
				continue
			}
			return result, fmt.Errorf("SuccessFactors RSS returned HTTP %d", response.StatusCode)
		}
		limited := io.LimitReader(response.Body, maxFeedBytes+1)
		counted := &countedReader{source: limited}
		buffered := bufio.NewReaderSize(counted, 64<<10)
		if !xmlHead(buffered) {
			response.Body.Close()
			result.Bytes += counted.n
			return result, errors.New("SuccessFactors feed returned non-XML content")
		}
		items, jobs, truncated, parseErr := ParseStream(buffered, emit)
		response.Body.Close()
		result.Bytes += counted.n
		result.Items, result.Jobs, result.Truncated = items, jobs, truncated
		if counted.n > maxFeedBytes {
			return result, fmt.Errorf("SuccessFactors RSS exceeded %d bytes", maxFeedBytes)
		}
		if parseErr != nil {
			return result, parseErr
		}
		return result, nil
	}
	return result, errors.New("SuccessFactors RSS retry budget exhausted")
}

func FetchFeed(ctx context.Context, feedURL string, emit func(Job) error) (Summary, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, feedURL, emit)
}
