package worker

import (
	"context"
	"crypto/sha256"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Session providers need the response cookie history as well as the sealed
// policy observation. The process-owned verified transport remains unchanged.
func fetchLastHTTPOnce(ctx context.Context, client *http.Client, scope providerResourceScope, r api.Request, limit int64) ([]byte, http.Header, *GreenhouseResponse, error) {
	if client == nil || !scope.ResourceMatches(r.URL) || limit < 1 || limit > 64<<20 || r.Method != "GET" && r.Method != "POST" || r.Method == "GET" && r.Body != "" {
		return nil, nil, nil, queue.ErrConfiguration
	}
	requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	request, e := http.NewRequestWithContext(requestCtx, r.Method, r.URL, strings.NewReader(r.Body))
	if e != nil {
		return nil, nil, nil, queue.ErrConfiguration
	}
	request.Header.Set("User-Agent", ordinaryUserAgent)
	request.Header.Set("Accept", ordinaryAccept)
	for key, values := range r.Headers {
		request.Header[key] = append([]string{}, values...)
	}
	response, e := client.Do(request)
	if e != nil {
		if ctx.Err() != nil {
			return nil, nil, nil, ctx.Err()
		}
		return nil, nil, nil, &DiscoveryError{Kind: "request_failed"}
	}
	defer response.Body.Close()
	if response.Request == nil || response.Request.URL == nil || !scope.ResourceMatches(response.Request.URL.String()) {
		return nil, nil, nil, queue.ErrConfiguration
	}
	headers := response.Header.Clone()
	observed := &GreenhouseResponse{endpoint: r.URL, finalURL: response.Request.URL.String(), status: response.StatusCode, location: headers.Get("Location"), contentType: headers.Get("Content-Type")}
	reservation, policyURL := headers.Get("TDM-Reservation"), headers.Get("TDM-Policy")
	signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
	check := func(source string) error {
		e := policy.Check(signals, source, observed.finalURL)
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			observed.reserved = true
			observed.policy = reserved.PolicyURL
			observed.reservationSource = reserved.Source
		}
		return e
	}
	if e = check(""); e != nil {
		return nil, headers, observed, e
	}
	if response.ContentLength > limit {
		return nil, headers, observed, &DiscoveryError{Kind: "body_limit"}
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, limit+1))
	observed.bytes = len(raw)
	if int64(len(raw)) > limit {
		return nil, headers, observed, &DiscoveryError{Kind: "body_limit"}
	}
	if e != nil {
		return nil, headers, observed, &DiscoveryError{Kind: "body_failed", cause: e}
	}
	if ctx.Err() != nil {
		return nil, headers, observed, ctx.Err()
	}
	if e = check(jsonld.DecodeDocument(raw, observed.contentType)); e != nil {
		return nil, headers, observed, e
	}
	if response.StatusCode != 200 && (r.Method != "POST" || response.StatusCode < 200 || response.StatusCode >= 300) {
		return raw, headers, observed, &DiscoveryError{Kind: "http_status", Status: response.StatusCode}
	}
	return raw, headers, observed, nil
}

type lastHTTPObservation struct {
	mu       sync.Mutex
	response *GreenhouseResponse
}

func (o *lastHTTPObservation) observe(response *GreenhouseResponse) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if response != nil && (o.response == nil || !o.response.reserved) {
		o.response = response
	}
}
func (o *lastHTTPObservation) latest() *GreenhouseResponse {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.response
}

func lastHTTPFetcher(client *http.Client, o api.LastHTTPOptions, scope providerResourceScope, wait func(context.Context, time.Duration) error, observe *lastHTTPObservation) (api.SuccessFactorsLegacyFetch, error) {
	if client == nil || wait == nil || observe == nil {
		return nil, queue.ErrConfiguration
	}
	sealed := *client
	jar, e := cookiejar.New(nil)
	if e != nil {
		return nil, e
	}
	// Apply the original response-cookie policy before storage. Automatic Go
	// synthesis would accept cookies rejected by Python and append jar cookies
	// to Infor's explicit SOAP Cookie header.
	sealed.Jar = nil
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return func(ctx context.Context, r api.Request) ([]byte, http.Header, error) {
		if !scope.ResourceMatches(r.URL) || r.Method != "GET" && r.Method != "POST" || r.Method == "POST" && (o.Provider != "peoplesoft" || r.URL != o.ListingURL()) {
			return nil, nil, queue.ErrConfiguration
		}
		bootstrap := o.Provider == "infor" && (strings.Contains(r.URL, "/CandidateSelfService/lm") || strings.Contains(r.URL, "/CandidateSelfService/controller.servlet")) || o.Provider == "peoplesoft" && strings.Contains(r.URL, "/psp/")
		attempts := 3
		if o.Provider == "infor" || r.Method == "POST" {
			attempts = 1
		}
		limit := int64(64 << 20)
		if o.Provider == "unisante" {
			limit = 512 * 1024
			if strings.Contains(r.URL, "/offre/") {
				limit = 1024 * 1024
			}
		}
		for attempt := 0; attempt < attempts; attempt++ {
			if ctx.Err() != nil {
				return nil, nil, ctx.Err()
			}
			current := r
			visited := map[string]bool{}
			cookies := []string{}
			var body []byte
			var headers http.Header
			var response *GreenhouseResponse
			var err error
			for redirects := 0; ; redirects++ {
				requestClient := sealed
				current.Headers = current.Headers.Clone()
				if current.Headers == nil {
					current.Headers = http.Header{}
				}
				requestURL, parseErr := url.Parse(current.URL)
				if parseErr != nil {
					return nil, nil, queue.ErrConfiguration
				}
				if current.Headers.Get("Cookie") == "" {
					request := &http.Request{Header: current.Headers}
					for _, cookie := range jar.Cookies(requestURL) {
						request.AddCookie(cookie)
					}
				}
				canonical := *requestURL
				canonical.Host = strings.ToLower(canonical.Host)
				state := sha256.Sum256([]byte(current.Headers.Get("Cookie")))
				visit := canonical.String() + string(state[:])
				if visited[visit] {
					return nil, nil, &DiscoveryError{Kind: "redirect"}
				}
				visited[visit] = true
				body, headers, response, err = fetchLastHTTPOnce(ctx, &requestClient, scope, current, limit)
				observe.observe(response)
				if headers != nil {
					jar.SetCookies(requestURL, api.OriginalSessionCookies(headers))
					cookies = append(cookies, headers.Values("Set-Cookie")...)
				}
				if response == nil || response.reserved || !bootstrap || response.status != 301 && response.status != 302 && response.status != 303 && response.status != 307 && response.status != 308 {
					break
				}
				next, e := pythonJoinURL(current.URL, response.location)
				// Infor returns to the initial URL with new cookies. State-aware
				// loop detection above and the original hard bound preserve it.
				if e != nil || response.location == "" || redirects >= 20 || !scope.ResourceMatches(next) {
					return nil, nil, queue.ErrConfiguration
				}
				current.URL = next
				// Redirect requests rebuild ordinary jar cookies for the new URL.
				current.Headers = r.Headers.Clone()
			}
			if ctx.Err() != nil {
				return nil, headers, ctx.Err()
			}
			var reserved *policy.Reservation
			if errors.As(err, &reserved) {
				return nil, headers, err
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if err == nil {
				if o.Provider != "infor" && len(body) == 0 {
					err = api.ErrInventory
				} else {
					if (o.Provider == "peoplesoft" || o.Provider == "papa_johns") && r.Method == "GET" {
						text := jsonld.DecodeDocument(body, response.contentType)
						if utf8.RuneCountInString(text) > 10_000_000 {
							runes := []rune(text)
							text = string(runes[:10_000_000])
						}
						body = []byte(text)
					} else if o.Provider != "infor" {
						body = []byte(jsonld.DecodeDocument(body, response.contentType))
					}
					if headers == nil {
						headers = http.Header{}
					}
					headers.Del("Set-Cookie")
					for _, cookie := range cookies {
						headers.Add("Set-Cookie", cookie)
					}
					return body, headers, nil
				}
			}
			var discovery *DiscoveryError
			terminalBody := errors.As(err, &discovery) && discovery.Kind == "body_limit"
			retry := !terminalBody && (status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status < 600 || status == 401 || status == 403 || o.Provider == "unisante" && status == 202)
			if !retry || attempt+1 == attempts {
				if o.Provider == "peoplesoft" && (status == 404 || status == 410) {
					return nil, headers, &DiscoveryError{Kind: "provider_gone", Status: status}
				}
				return nil, headers, &remainingHTTPStatusError{status: status, cause: err}
			}
			if e := wait(ctx, time.Duration(float64(500*time.Millisecond)*float64(uint64(1)<<uint(attempt))*(0.5+rand.Float64()))); e != nil {
				return nil, headers, e
			}
		}
		return nil, nil, api.ErrInventory
	}, nil
}

func FetchLastHTTPProviders(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.LastHTTPOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if e != nil || config["crawler_type"] != p.Provider || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	observation := &lastHTTPObservation{}
	fetch, e := lastHTTPFetcher(client, o, o, wait, observation)
	if e != nil {
		return out, e
	}
	plain := func(ctx context.Context, r api.Request) ([]byte, error) { b, _, e := fetch(ctx, r); return b, e }
	var jobs []api.Job
	switch o.Provider {
	case "papa_johns":
		var urls []string
		urls, e = api.DiscoverPapaJohns(ctx, o, plain)
		for _, source := range urls {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
		}
	case "infor":
		jobs, e = api.DiscoverInfor(ctx, o, fetch)
	case "peoplesoft":
		jobs, e = api.DiscoverPeopleSoft(ctx, o, plain)
	case "unisante":
		zone, zoneErr := time.LoadLocation("Europe/Zurich")
		if zoneErr != nil {
			return out, zoneErr
		}
		jobs, e = api.DiscoverUnisante(ctx, o, time.Now().In(zone).Format("2006-01-02"), plain, enrichment.NormalizeDescriptionHTML)
	default:
		return out, queue.ErrConfiguration
	}
	out.Response = observation.latest()
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	for _, j := range jobs {
		var extras map[string]any
		for key, value := range j.Extras {
			if key != "language" {
				if extras == nil {
					extras = map[string]any{}
				}
				extras[key] = value
			}
		}
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "date_posted": j.DatePosted, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "metadata": j.Metadata, "extras": extras}
		if j.Extras != nil {
			fields["language"] = j.Extras["language"]
		}
		job, e := secondaryRichJob(fields)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		job.SourceIdentity = j.SourceIdentity
		out.Jobs = append(out.Jobs, job)
	}
	out.Truncated = len(out.Jobs) > 50_000
	if out.Truncated {
		out.Jobs = out.Jobs[:50_000]
	}
	return out, nil
}
