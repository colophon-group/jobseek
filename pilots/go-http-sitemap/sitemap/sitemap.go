package sitemap

import (
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
)

const (
	maxProtocolURLs        = 50_000
	defaultRootMaxAttempts = 3
	maxRootMaxAttempts     = 3
	defaultRootBackoff     = 500 * time.Millisecond
)

var sitemapHeaders = http.Header{
	"User-Agent": {"jobseek-crawler (+https://jseek.co/)"},
	"Accept":     {"application/xml,text/xml,*/*;q=0.8"},
}

type ErrorKind string

const (
	ErrorConfig      ErrorKind = "config"
	ErrorStatus      ErrorKind = "status"
	ErrorXML         ErrorKind = "xml"
	ErrorUnsupported ErrorKind = "unsupported"
	ErrorCanceled    ErrorKind = "canceled"
	ErrorDeadline    ErrorKind = "deadline"
	ErrorEmptyBody   ErrorKind = "empty_body"
)

type Error struct {
	Kind   ErrorKind
	URL    string
	Status int
	Err    error
}

func (e *Error) Error() string {
	if e.Status != 0 {
		return fmt.Sprintf("sitemap %s for %s (status=%d)", e.Kind, e.URL, e.Status)
	}
	return fmt.Sprintf("sitemap %s for %s", e.Kind, e.URL)
}

func (e *Error) Unwrap() error { return e.Err }

func newError(kind ErrorKind, rawURL string, status int, err error) *Error {
	return &Error{Kind: kind, URL: redactedURL(rawURL), Status: status, Err: err}
}

// RetryExhaustedError records why the explicit root request consumed its
// bounded attempt budget. Child sitemap requests are never retried.
type RetryExhaustedError struct {
	URL               string
	Attempts          int
	LastStatus        int
	LastTransportKind boundedhttp.ErrorKind
	LastOutcome       string
}

func (e *RetryExhaustedError) Error() string {
	if e.LastOutcome == "empty_body" {
		return fmt.Sprintf("sitemap root retry exhausted for %s after %d attempts (status=200 outcome=empty_body)", e.URL, e.Attempts)
	}
	if e.LastStatus != 0 {
		return fmt.Sprintf("sitemap root retry exhausted for %s after %d attempts (status=%d)", e.URL, e.Attempts, e.LastStatus)
	}
	return fmt.Sprintf("sitemap root retry exhausted for %s after %d attempts (transport=%s)", e.URL, e.Attempts, e.LastTransportKind)
}

type Config struct {
	SitemapURL       string
	IncludeLiteral   string
	ExcludeLiteral   string
	ReplacePrefix    string
	Replacement      string
	MaxURLs          int
	MaxIndexChildren int
	RootMaxAttempts  int
	RootBackoff      time.Duration
}

type Result struct {
	URLs             []string
	NewSitemapURL    string
	FilteredCount    int
	Truncated        bool
	TransportMetrics boundedhttp.Stats
}

type Runner struct {
	client *boundedhttp.Client
	config Config
	sleep  func(context.Context, time.Duration) error
}

type sessionGetter interface {
	Get(context.Context, string, http.Header) (boundedhttp.Response, error)
}

type document struct {
	XMLName  xml.Name
	URLs     []location `xml:"url"`
	Children []location `xml:"sitemap"`
}

type location struct {
	Loc string `xml:"loc"`
}

func New(client *boundedhttp.Client, config Config) (*Runner, error) {
	if config.RootMaxAttempts == 0 {
		config.RootMaxAttempts = defaultRootMaxAttempts
	}
	if config.RootBackoff == 0 {
		config.RootBackoff = defaultRootBackoff
	}
	if client == nil || config.SitemapURL == "" || config.MaxURLs <= 0 || config.MaxURLs > maxProtocolURLs || config.MaxIndexChildren <= 0 {
		return nil, newError(ErrorConfig, config.SitemapURL, 0, nil)
	}
	if config.RootMaxAttempts < 1 || config.RootMaxAttempts > maxRootMaxAttempts || config.RootBackoff < 0 || config.RootBackoff > time.Duration(math.MaxInt64/2) {
		return nil, newError(ErrorConfig, config.SitemapURL, 0, nil)
	}
	parsed, err := url.Parse(config.SitemapURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return nil, newError(ErrorConfig, config.SitemapURL, 0, nil)
	}
	if (config.ReplacePrefix == "") != (config.Replacement == "") {
		return nil, newError(ErrorConfig, config.SitemapURL, 0, nil)
	}
	return &Runner{client: client, config: config, sleep: sleepContext}, nil
}

func (r *Runner) Run(ctx context.Context) (Result, error) {
	session := r.client.NewSession()
	fail := func(err error) (Result, error) {
		return Result{TransportMetrics: session.Stats()}, err
	}
	doc, err := r.fetchRoot(ctx, session)
	if err != nil {
		return fail(err)
	}

	var rawURLs []string
	switch doc.XMLName.Local {
	case "urlset":
		rawURLs = extractURLs(doc)
	case "sitemapindex":
		children := extractChildren(doc)
		jobChildren := make([]string, 0, len(children))
		for _, child := range children {
			if isJobRelated(child) {
				jobChildren = append(jobChildren, child)
			}
		}
		if len(jobChildren) > 0 {
			children = jobChildren
		}
		children = dedupeFirstSeen(children)
		if len(children) > r.config.MaxIndexChildren {
			return fail(newError(ErrorUnsupported, r.config.SitemapURL, 0, nil))
		}
		usableChildren := 0
		for _, childURL := range children {
			child, childMissing, childErr := fetchDocument(ctx, session, childURL, true)
			if childErr != nil {
				return fail(childErr)
			}
			if childMissing {
				continue
			}
			if child.XMLName.Local != "urlset" {
				return fail(newError(ErrorUnsupported, childURL, 0, nil))
			}
			usableChildren++
			rawURLs = append(rawURLs, extractURLs(child)...)
		}
		if usableChildren == 0 {
			return fail(newError(ErrorUnsupported, r.config.SitemapURL, 0, nil))
		}
	default:
		return fail(newError(ErrorUnsupported, r.config.SitemapURL, 0, nil))
	}

	canonicalURLs := make([]string, 0, len(rawURLs))
	for _, raw := range rawURLs {
		canonicalURLs = append(canonicalURLs, stripUTM(strings.TrimSpace(raw)))
	}
	truncated := len(canonicalURLs) > r.config.MaxURLs
	if truncated {
		sort.Strings(canonicalURLs)
		canonicalURLs = canonicalURLs[:r.config.MaxURLs]
	}
	uniqueCanonicalURLs := make(map[string]struct{}, len(canonicalURLs))
	for _, candidate := range canonicalURLs {
		if candidate != "" {
			uniqueCanonicalURLs[candidate] = struct{}{}
		}
	}

	urls := make(map[string]struct{}, len(uniqueCanonicalURLs))
	filtered := 0
	for candidate := range uniqueCanonicalURLs {
		if r.config.IncludeLiteral != "" && !strings.Contains(candidate, r.config.IncludeLiteral) {
			filtered++
			continue
		}
		if r.config.ExcludeLiteral != "" && strings.Contains(candidate, r.config.ExcludeLiteral) {
			filtered++
			continue
		}
		if r.config.ReplacePrefix != "" && strings.HasPrefix(candidate, r.config.ReplacePrefix) {
			candidate = r.config.Replacement + strings.TrimPrefix(candidate, r.config.ReplacePrefix)
		}
		if candidate != "" {
			urls[candidate] = struct{}{}
		}
	}

	sortedURLs := make([]string, 0, len(urls))
	for candidate := range urls {
		sortedURLs = append(sortedURLs, candidate)
	}
	sort.Strings(sortedURLs)
	return Result{
		URLs:             sortedURLs,
		FilteredCount:    filtered,
		Truncated:        truncated,
		TransportMetrics: session.Stats(),
	}, nil
}

func (r *Runner) fetchRoot(ctx context.Context, session sessionGetter) (document, error) {
	for attempt := 1; attempt <= r.config.RootMaxAttempts; attempt++ {
		doc, _, err := fetchDocument(ctx, session, r.config.SitemapURL, false)
		if err == nil {
			return doc, nil
		}
		if callerErr := ctx.Err(); callerErr != nil {
			kind := ErrorCanceled
			if errors.Is(callerErr, context.DeadlineExceeded) {
				kind = ErrorDeadline
			}
			return document{}, newError(kind, r.config.SitemapURL, 0, callerErr)
		}
		retry, lastStatus, lastTransportKind, lastOutcome := retryableRootFailure(err)
		if !retry {
			return document{}, err
		}
		if attempt == r.config.RootMaxAttempts {
			return document{}, &RetryExhaustedError{
				URL:               redactedURL(r.config.SitemapURL),
				Attempts:          attempt,
				LastStatus:        lastStatus,
				LastTransportKind: lastTransportKind,
				LastOutcome:       lastOutcome,
			}
		}
		if err := r.sleep(ctx, r.config.RootBackoff*time.Duration(1<<(attempt-1))); err != nil {
			kind := ErrorCanceled
			if errors.Is(err, context.DeadlineExceeded) {
				kind = ErrorDeadline
			}
			return document{}, newError(kind, r.config.SitemapURL, 0, err)
		}
	}
	panic("unreachable")
}

func retryableRootFailure(err error) (bool, int, boundedhttp.ErrorKind, string) {
	var sitemapErr *Error
	if errors.As(err, &sitemapErr) {
		if sitemapErr.Kind == ErrorEmptyBody {
			return true, http.StatusOK, "", "empty_body"
		}
		status := sitemapErr.Status
		return status == http.StatusAccepted || status == http.StatusUnauthorized || status == http.StatusForbidden || status == http.StatusRequestTimeout || status == http.StatusTooEarly || status == http.StatusTooManyRequests || status >= 500 && status <= 599, status, "", ""
	}
	var transportErr *boundedhttp.Error
	if errors.As(err, &transportErr) && (transportErr.Kind == boundedhttp.ErrorTimeout || transportErr.Kind == boundedhttp.ErrorTransport) {
		return true, 0, transportErr.Kind, ""
	}
	return false, 0, "", ""
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

func fetchDocument(ctx context.Context, session sessionGetter, rawURL string, child bool) (document, bool, error) {
	response, err := session.Get(ctx, rawURL, sitemapHeaders)
	if err != nil {
		return document{}, false, err
	}
	if child && (response.StatusCode == http.StatusNotFound || response.StatusCode == http.StatusGone) {
		return document{}, true, nil
	}
	if response.StatusCode != http.StatusOK {
		return document{}, false, newError(ErrorStatus, rawURL, response.StatusCode, nil)
	}
	if len(response.Body) == 0 {
		return document{}, false, newError(ErrorEmptyBody, rawURL, http.StatusOK, nil)
	}
	var parsed document
	if err := xml.Unmarshal(response.Body, &parsed); err != nil {
		return document{}, false, newError(ErrorXML, rawURL, 0, err)
	}
	return parsed, false, nil
}

func extractURLs(doc document) []string {
	result := make([]string, 0, len(doc.URLs))
	for _, entry := range doc.URLs {
		if strings.TrimSpace(entry.Loc) != "" {
			result = append(result, entry.Loc)
		}
	}
	return result
}

func extractChildren(doc document) []string {
	result := make([]string, 0, len(doc.Children))
	for _, entry := range doc.Children {
		if strings.TrimSpace(entry.Loc) != "" {
			result = append(result, strings.TrimSpace(entry.Loc))
		}
	}
	return result
}

func dedupeFirstSeen(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	return result
}

func isJobRelated(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	path := strings.ToLower(parsed.Path)
	for _, keyword := range []string{"job", "career", "posting", "position", "vacancy", "opening"} {
		if strings.Contains(path, keyword) {
			return true
		}
	}
	return false
}

func stripUTM(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.RawQuery == "" {
		return rawURL
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil {
		return rawURL
	}
	orderedKeys := make([]string, 0, len(query))
	seen := make(map[string]struct{}, len(query))
	for _, part := range strings.Split(parsed.RawQuery, "&") {
		rawKey, _, _ := strings.Cut(part, "=")
		key, err := url.QueryUnescape(rawKey)
		if err != nil {
			return rawURL
		}
		if _, ok := query[key]; !ok {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		orderedKeys = append(orderedKeys, key)
	}
	filtered := make([]string, 0, len(query))
	for _, key := range orderedKeys {
		if strings.HasPrefix(key, "utm_") {
			continue
		}
		for _, value := range query[key] {
			filtered = append(filtered, url.QueryEscape(key)+"="+url.QueryEscape(value))
		}
	}
	parsed.RawQuery = strings.Join(filtered, "&")
	parsed.ForceQuery = false
	return parsed.String()
}

func redactedURL(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "<invalid-url>"
	}
	parsed.User = nil
	parsed.RawQuery = ""
	parsed.ForceQuery = false
	parsed.Fragment = ""
	return parsed.String()
}
