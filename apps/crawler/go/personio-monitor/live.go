package personio

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maxXMLBytes = 128 << 20
const maxHTMLBytes = 16 << 20
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

var languagePattern = regexp.MustCompile(`^[a-z]{2}$`)

type FetchResult struct {
	Jobs      []Job  `json:"jobs"`
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
		return nil, errors.New("Personio host has no DNS addresses")
	}
	for _, answer := range answers {
		ip, valid := netip.AddrFromSlice(answer.IP)
		if !valid || !publicAddress(ip.Unmap()) {
			return nil, errors.New("Personio host resolved to a non-public address")
		}
	}
	dialer := net.Dialer{Timeout: 30 * time.Second}
	for _, answer := range answers {
		if conn, err := dialer.DialContext(ctx, network, net.JoinHostPort(answer.IP.String(), port)); err == nil {
			return conn, nil
		}
	}
	return nil, errors.New("Personio host has no reachable public address")
}

func validRedirectURL(parsed *url.URL) error {
	if parsed == nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.Port() != "" && parsed.Port() != "443" {
		return errors.New("Personio redirect is not canonical HTTPS")
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
				return errors.New("Personio redirected more than three times")
			}
			return validRedirectURL(request.URL)
		},
	}
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

func endpoint(slug, domain, path, language string) string {
	base := "https://" + slug + ".jobs.personio." + domain + path
	if language != "" {
		return base + "?language=" + url.QueryEscape(language)
	}
	return base
}

func get(ctx context.Context, client requestDoer, result *FetchResult, address string) (*http.Response, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", userAgent)
	result.Requests++
	response, err := client.Do(request)
	if err != nil {
		return nil, err
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
		return nil, errors.New("tdm-reservation=1")
	}
	return response, nil
}

func fetchXML(ctx context.Context, client requestDoer, result *FetchResult, slug, domain, language string) ([]Job, bool, error) {
	response, err := get(ctx, client, result, endpoint(slug, domain, "/xml", language))
	if err != nil {
		if result.ErrorKind == "tdm" || ctx.Err() != nil {
			return nil, false, err
		}
		return nil, false, nil
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, false, nil
	}
	reader := &countedReader{source: io.LimitReader(response.Body, maxXMLBytes+1)}
	jobs := []Job{}
	_, _, parseErr := ParseReader(reader, slug, domain, func(job Job) error {
		if len(jobs) <= MaxJobs {
			jobs = append(jobs, job)
		}
		return nil
	})
	result.Bytes += reader.n
	if reader.n > maxXMLBytes {
		return nil, false, errors.New("Personio XML feed exceeded 128 MiB")
	}
	if parseErr != nil {
		return nil, false, nil
	}
	return jobs, true, nil
}

func fetchHTML(ctx context.Context, client requestDoer, result *FetchResult, slug, domain string) ([]Job, error) {
	response, err := get(ctx, client, result, endpoint(slug, domain, "/", ""))
	if err != nil {
		if result.ErrorKind == "tdm" || ctx.Err() != nil {
			return nil, err
		}
		return nil, nil
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, nil
	}
	body, readErr := io.ReadAll(io.LimitReader(response.Body, maxHTMLBytes+1))
	result.Bytes += int64(len(body))
	if readErr != nil {
		return nil, nil
	}
	if len(body) > maxHTMLBytes {
		return nil, errors.New("Personio HTML listing exceeded 16 MiB")
	}
	return ParseHTMLListings(string(body), slug, domain)
}

func localizedFields(job Job) map[string]any {
	fields := map[string]any{}
	if job.Title != nil {
		fields["title"] = *job.Title
	}
	if job.Description != nil {
		fields["description"] = *job.Description
	}
	if len(job.Locations) > 0 {
		fields["locations"] = job.Locations
	}
	return fields
}

func addLocalization(jobs []Job, alternate []Job, primary, language string) {
	byID := map[string]map[string]any{}
	for _, job := range alternate {
		id, ok := job.Metadata["id"].(string)
		if !ok || id == "" {
			continue
		}
		if fields := localizedFields(job); len(fields) > 0 {
			byID[id] = fields
		}
	}
	for index := range jobs {
		id, ok := jobs[index].Metadata["id"].(string)
		if !ok {
			continue
		}
		fields, exists := byID[id]
		if !exists {
			continue
		}
		if jobs[index].Localizations == nil {
			jobs[index].Localizations = map[string]any{primary: localizedFields(jobs[index])}
		}
		jobs[index].Localizations[language] = fields
		if jobs[index].Description == nil {
			if alternateDescription, ok := fields["description"].(string); ok {
				jobs[index].Description = &alternateDescription
			}
		}
	}
}

func promoteEnglish(jobs []Job, primary string) {
	for index := range jobs {
		english, ok := jobs[index].Localizations["en"].(map[string]any)
		if !ok {
			continue
		}
		jobs[index].Localizations[primary] = localizedFields(jobs[index])
		if value, ok := english["title"].(string); ok && value != "" {
			jobs[index].Title = &value
		}
		if value, ok := english["description"].(string); ok && value != "" {
			jobs[index].Description = &value
		}
		if value, ok := english["locations"].([]string); ok && len(value) > 0 {
			jobs[index].Locations = value
		}
		language := "en"
		jobs[index].Language = &language
	}
}

// Fetch follows the existing XML domain and language order, then its RSC HTML
// fallback. Each origin request is exclusive to this Go invocation.
func Fetch(ctx context.Context, client requestDoer, slug, preferredDomain, language string, backfill []string) (FetchResult, error) {
	result := FetchResult{Jobs: []Job{}}
	if !slugPattern.MatchString(slug) || preferredDomain != "de" && preferredDomain != "com" || !languagePattern.MatchString(language) {
		return result, errors.New("invalid Personio source configuration")
	}
	seen := map[string]bool{}
	for _, alternate := range backfill {
		if !languagePattern.MatchString(alternate) || seen[alternate] {
			return result, errors.New("invalid Personio backfill language")
		}
		seen[alternate] = true
	}
	domains := []string{preferredDomain, "de"}
	if preferredDomain == "de" {
		domains[1] = "com"
	}
	for _, domain := range domains {
		jobs, available, err := fetchXML(ctx, client, &result, slug, domain, language)
		if err != nil {
			return result, err
		}
		if !available {
			continue
		}
		if len(jobs) > MaxJobs {
			result.Truncated = true
			jobs = jobs[:MaxJobs]
		}
		result.Jobs = jobs
		primaryStatus, primaryURL := result.Status, result.FinalURL
		if len(jobs) == 0 {
			return result, nil
		}
		for index := range result.Jobs {
			result.Jobs[index].Language = &language
		}
		for _, alternate := range backfill {
			if alternate == language {
				continue
			}
			other, ok, err := fetchXML(ctx, client, &result, slug, domain, alternate)
			if err != nil {
				return result, err
			}
			if !ok || len(other) == 0 {
				continue
			}
			addLocalization(result.Jobs, other, language, alternate)
		}
		if language != "en" {
			promoteEnglish(result.Jobs, language)
		}
		// Alternate-language misses do not change the authoritative primary
		// feed response, just as the existing Python monitor keeps its jobs.
		result.Status, result.FinalURL = primaryStatus, primaryURL
		return result, nil
	}
	for _, domain := range domains {
		jobs, err := fetchHTML(ctx, client, &result, slug, domain)
		if err != nil {
			return result, err
		}
		if len(jobs) == 0 {
			continue
		}
		if len(jobs) > MaxJobs {
			result.Truncated = true
			jobs = jobs[:MaxJobs]
		}
		result.Jobs = jobs
		return result, nil
	}
	return result, fmt.Errorf("Personio feed unavailable for slug %q", slug)
}

func FetchFeed(ctx context.Context, slug, preferredDomain, language string, backfill []string) (FetchResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return Fetch(ctx, client, slug, preferredDomain, language, backfill)
}
