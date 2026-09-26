package workable

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
	"net/netip"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maxResponseBytes = 64 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

var metaRE = regexp.MustCompile(`(?is)<meta\b[^>]*>`)
var tdmNameRE = regexp.MustCompile(`(?i)\bname\s*=\s*["']?tdm-reservation(?:["']|\s|/?>)`)
var tdmContentRE = regexp.MustCompile(`(?i)\bcontent\s*=\s*["']?1(?:["']|\s|/?>)`)

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
type pauseFunc func(context.Context, time.Duration) error

func normalPause(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func retryPause(ctx context.Context, attempt int, base time.Duration, pause pauseFunc) error {
	bound := base << attempt
	return pause(ctx, time.Duration(rand.Int63n(int64(bound))))
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

func tdmMetaReserved(body []byte) bool {
	for _, tag := range metaRE.FindAll(body, -1) {
		if tdmNameRE.Match(tag) && tdmContentRE.Match(tag) {
			return true
		}
	}
	return false
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
	if err != nil || len(answers) == 0 {
		return nil, errors.New("Workable host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("Workable host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		if conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port)); err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("Workable host has no reachable public address")
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	return &http.Client{
		Timeout:   30 * time.Second,
		Transport: transport,
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) > 3 || len(via) == 0 || via[0].URL.Hostname() != "www.workable.com" {
				return http.ErrUseLastResponse
			}
			if request.URL.Scheme != "https" || request.URL.Hostname() != "www.workable.com" || request.URL.User != nil || request.URL.Port() != "" {
				return http.ErrUseLastResponse
			}
			return nil
		},
	}
}

func fetchBody(ctx context.Context, client requestDoer, result *FetchResult, method, endpoint string, postBody []byte) ([]byte, int, error) {
	var input io.Reader
	if postBody != nil {
		input = bytes.NewReader(postBody)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, input)
	if err != nil {
		return nil, 0, err
	}
	request.Header.Set("User-Agent", userAgent)
	if postBody != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	result.Status = 0
	result.Requests++
	response, err := client.Do(request)
	if err != nil {
		return nil, 0, err
	}
	defer response.Body.Close()
	result.Responses++
	result.Status = response.StatusCode
	result.FinalURL = endpoint
	if response.Request != nil && response.Request.URL != nil {
		result.FinalURL = response.Request.URL.String()
	}
	if result.FinalURL != endpoint {
		return nil, response.StatusCode, errors.New("Workable request redirected from its selected endpoint")
	}
	if tdmReserved(response.Header.Get("TDM-Reservation")) {
		result.ErrorKind = "tdm"
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return nil, response.StatusCode, errors.New("tdm-reservation=1")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
	result.Bytes += int64(len(body))
	if err != nil {
		return nil, response.StatusCode, err
	}
	if len(body) > maxResponseBytes {
		return nil, response.StatusCode, errors.New("Workable response exceeded 64 MiB")
	}
	if tdmMetaReserved(body) {
		result.ErrorKind = "tdm"
		result.TDMPolicy = response.Header.Get("TDM-Policy")
		return nil, response.StatusCode, errors.New("tdm-reservation=1")
	}
	return body, response.StatusCode, nil
}

func markdownInventory(ctx context.Context, client requestDoer, result *FetchResult, slug string, pause pauseFunc) error {
	base := "https://apply.workable.com/" + slug + "/"
	readText := func(endpoint string) ([]byte, error) {
		for attempt := 0; attempt < 3; attempt++ {
			body, status, err := fetchBody(ctx, client, result, http.MethodGet, endpoint, nil)
			if err == nil && status == 200 && len(bytes.TrimSpace(body)) > 0 {
				return body, nil
			}
			if err == nil {
				err = fmt.Errorf("Workable Markdown returned HTTP %d or empty content", status)
			}
			if result.ErrorKind == "tdm" || status != 0 && status != 200 && !retryable(status) || attempt == 2 {
				return nil, err
			}
			if err := retryPause(ctx, attempt, 500*time.Millisecond, pause); err != nil {
				return nil, err
			}
		}
		return nil, errors.New("unreachable Workable Markdown retry state")
	}
	llms, err := readText(base + "llms.txt")
	if err != nil {
		return err
	}
	advertised, err := parseAdvertisedCount(llms)
	if err != nil {
		return err
	}
	if advertised == 0 {
		result.VerifiedEmpty = true
		return nil
	}
	jobs, err := readText(base + "jobs.md")
	if err != nil {
		return err
	}
	urls := parseMarkdownURLs(slug, jobs)
	if len(urls) == 0 && bytes.Contains(jobs, []byte("Use the search endpoint to filter results")) {
		endpoint := "https://www.workable.com/api/accounts/" + slug
		for attempt := 0; attempt < 3; attempt++ {
			body, status, fetchErr := fetchBody(ctx, client, result, http.MethodGet, endpoint, nil)
			if fetchErr == nil && status == 200 {
				urls, fetchErr = parsePublicAPIURLs(slug, body)
			}
			if fetchErr == nil && status == 200 {
				break
			}
			if fetchErr == nil {
				fetchErr = fmt.Errorf("Workable public API returned HTTP %d", status)
			}
			if result.ErrorKind == "tdm" || status != 0 && status != 200 && !retryable(status) || attempt == 2 {
				return fetchErr
			}
			if err := retryPause(ctx, attempt, 500*time.Millisecond, pause); err != nil {
				return err
			}
		}
		if err := checkAdvertised(urls, advertised, "public API"); err != nil {
			return err
		}
	} else if err := checkAdvertised(urls, advertised, "Markdown"); err != nil {
		return err
	}
	result.URLs = urls
	return nil
}

// Fetch performs the same Workable list pagination and 429 fallback as the
// Python monitor. A failed page never produces a partial successful set.
func Fetch(ctx context.Context, client requestDoer, slug string, pause pauseFunc) (FetchResult, error) {
	result := FetchResult{Inventory: Inventory{URLs: []string{}}}
	if !validSlug(slug) {
		return result, errors.New("Workable slug is invalid")
	}
	if pause == nil {
		pause = normalPause
	}
	endpoint := "https://apply.workable.com/api/v3/accounts/" + slug + "/jobs"
	requestBody := map[string]any{"query": "", "location": []string{}, "department": []string{}, "worktype": []string{}}
	seen := map[string]struct{}{}
	for {
		var pageURLs []string
		var next json.RawMessage
		var hasNext bool
		for attempt := 0; attempt < 4; attempt++ {
			postBody, err := json.Marshal(requestBody)
			if err != nil {
				return result, err
			}
			body, status, fetchErr := fetchBody(ctx, client, &result, http.MethodPost, endpoint, postBody)
			if fetchErr == nil && status == 200 {
				pageURLs, next, hasNext, fetchErr = ParsePage(slug, body)
			}
			if fetchErr == nil && status == 200 {
				break
			}
			if fetchErr == nil {
				fetchErr = fmt.Errorf("Workable list returned HTTP %d", status)
			}
			if result.ErrorKind == "tdm" || status != 0 && status != 200 && !retryable(status) || attempt == 3 {
				if status == 429 && len(seen) == 0 && result.ErrorKind != "tdm" {
					if err := markdownInventory(ctx, client, &result, slug, pause); err != nil {
						return result, err
					}
					return result, nil
				}
				return result, fetchErr
			}
			if err := retryPause(ctx, attempt, 5*time.Second, pause); err != nil {
				return result, err
			}
		}
		for _, jobURL := range pageURLs {
			seen[jobURL] = struct{}{}
		}
		if !hasNext {
			result.URLs = sortedURLs(seen)
			return result, nil
		}
		if len(seen) >= MaxJobs {
			result.URLs = sortedURLs(seen)
			result.Truncated = true
			return result, nil
		}
		requestBody["token"] = next
	}
}

func FetchSlug(ctx context.Context, slug string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, slug, normalPause)
}
