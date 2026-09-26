package workday

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const (
	detailAttempts = 3
	maxDetailBody  = 2 << 20
)

var (
	detailHostRE = regexp.MustCompile(`^([A-Za-z0-9_-]+)\.(wd[0-9]+)\.myworkdayjobs\.com$`)
	detailPathRE = regexp.MustCompile(`^/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9][A-Za-z0-9_-]{0,127})(/job/.+)$`)
)

type DetailFetchResult struct {
	Content         DetailContent
	Gone            bool
	Requests        int
	Responses       int
	TransportErrors int
	Bytes           int64
	Status          int
	TDMPolicy       string
}

type DetailFetchError struct {
	Status      int
	Kind        string
	Attempts    int
	ContentType string
	BodyLength  int
	BodySHA256  string
	Cause       error
}

func (e *DetailFetchError) Error() string {
	if e.Kind == "invalid_payload" {
		return fmt.Sprintf("Workday detail payload remained invalid after %d attempts (status=%d, content_type=%q, body_length=%d, body_sha256=%s): %v", e.Attempts, e.Status, e.ContentType, e.BodyLength, e.BodySHA256, e.Cause)
	}
	return fmt.Sprintf("Workday detail fetch failed (kind=%s, status=%d): %v", e.Kind, e.Status, e.Cause)
}

func (e *DetailFetchError) Unwrap() error { return e.Cause }

func workdayDetailAPIURL(rawURL string) (string, string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Port() != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", "", errors.New("Workday detail URL is not a canonical HTTPS job URL")
	}
	host := detailHostRE.FindStringSubmatch(parsed.Hostname())
	path := detailPathRE.FindStringSubmatch(parsed.Path)
	escapedPath := detailPathRE.FindStringSubmatch(parsed.EscapedPath())
	if host == nil || path == nil || escapedPath == nil || path[1] != escapedPath[1] ||
		strings.Contains(path[2], "..") || strings.ContainsAny(path[2], "\\\x00\r\n") ||
		strings.Contains(strings.ToLower(escapedPath[2]), "%2f") ||
		strings.Contains(strings.ToLower(escapedPath[2]), "%5c") ||
		strings.Contains(strings.ToLower(escapedPath[2]), "%2e") {
		return "", "", errors.New("Workday detail URL has an unsupported host or path")
	}
	api := fmt.Sprintf("https://%s/wday/cxs/%s/%s%s", parsed.Hostname(), host[1], path[1], escapedPath[2])
	return api, host[1], nil
}

// FetchWorkdayDetail owns one direct, SSRF-guarded detail GET. Invalid HTTP-200
// payloads receive the Python scraper's three-attempt bounded retry; 404 and
// Workday S22 are authoritative gone responses. Other HTTP failures are left
// to the existing queue backoff rather than retried inside this scraper.
func FetchWorkdayDetail(ctx context.Context, rawURL string, aliases []string) (DetailFetchResult, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DialContext = publicDialContext
	transport.ForceAttemptHTTP2 = true
	client := &http.Client{
		Timeout:       30 * time.Second,
		Transport:     transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	defer transport.CloseIdleConnections()
	return fetchWorkdayDetail(ctx, rawURL, aliases, client, sleepContext, rand.Float64)
}

func fetchWorkdayDetail(
	ctx context.Context, rawURL string, aliases []string, client *http.Client,
	sleep func(context.Context, time.Duration) error, random func() float64,
) (DetailFetchResult, error) {
	apiURL, tenant, err := workdayDetailAPIURL(rawURL)
	if err != nil {
		return DetailFetchResult{}, err
	}
	if client == nil || sleep == nil || random == nil {
		return DetailFetchResult{}, errors.New("Workday detail fetch dependencies are required")
	}
	result := DetailFetchResult{}
	for attempt := 1; attempt <= detailAttempts; attempt++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
		if err != nil {
			return result, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", "application/json")
		result.Requests++
		response, err := client.Do(request)
		if err != nil {
			result.TransportErrors++
			return result, &DetailFetchError{Kind: "transport", Attempts: attempt, Cause: err}
		}
		result.Responses++
		result.Status = response.StatusCode
		body, readErr := io.ReadAll(io.LimitReader(response.Body, maxDetailBody+1))
		response.Body.Close()
		result.Bytes += int64(len(body))
		if readErr != nil {
			return result, &DetailFetchError{Status: response.StatusCode, Kind: "transport", Attempts: attempt, Cause: fmt.Errorf("read Workday detail: %w", readErr)}
		}
		if len(body) > maxDetailBody {
			return result, &DetailFetchError{Status: response.StatusCode, Kind: "body_limit", Attempts: attempt, BodyLength: len(body), Cause: errors.New("Workday detail exceeds body limit")}
		}
		if strings.TrimSpace(response.Header.Get("TDM-Reservation")) == "1" {
			result.TDMPolicy = response.Header.Get("TDM-Policy")
			return result, &ReservationError{URL: apiURL, PolicyURL: result.TDMPolicy}
		}
		if response.StatusCode == http.StatusNotFound || response.StatusCode == http.StatusForbidden && workdayS22(body) {
			result.Gone = true
			return result, nil
		}
		if response.StatusCode != http.StatusOK {
			return result, &DetailFetchError{Status: response.StatusCode, Kind: "http", Attempts: attempt, Cause: errors.New("upstream returned a non-success status")}
		}
		var envelope struct {
			JobPostingInfo json.RawMessage `json:"jobPostingInfo"`
		}
		validEnvelope := json.Unmarshal(body, &envelope) == nil && len(envelope.JobPostingInfo) > 0 && envelope.JobPostingInfo[0] == '{'
		if validEnvelope {
			content, err := ProjectDetail(body, tenant, aliases)
			if err != nil {
				return result, &DetailFetchError{Status: response.StatusCode, Kind: "projection", Attempts: attempt, Cause: err}
			}
			result.Content = content
			return result, nil
		}
		digest := sha256.Sum256(body)
		if attempt == detailAttempts {
			return result, &DetailFetchError{
				Status: response.StatusCode, Kind: "invalid_payload", Attempts: attempt,
				ContentType: response.Header.Get("Content-Type"), BodyLength: len(body),
				BodySHA256: fmt.Sprintf("%x", digest[:8]), Cause: errors.New("missing or malformed jobPostingInfo"),
			}
		}
		delay := time.Duration(float64(500*time.Millisecond<<uint(attempt-1)) * (0.5 + random()))
		if err := sleep(ctx, delay); err != nil {
			return result, err
		}
	}
	return result, errors.New("unreachable Workday detail retry state")
}

func workdayS22(body []byte) bool {
	var response struct {
		ErrorCode string `json:"errorCode"`
	}
	return json.Unmarshal(body, &response) == nil && response.ErrorCode == "S22"
}
