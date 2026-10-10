package worker

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"mime"
	"net/http"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type remainingHTTPStatusError struct {
	status int
	cause  error
}

func (e *remainingHTTPStatusError) Error() string       { return "provider HTTP request failed" }
func (e *remainingHTTPStatusError) Unwrap() error       { return e.cause }
func (e *remainingHTTPStatusError) HTTPStatusCode() int { return e.status }

func fetchRemainingHTTPResource(ctx context.Context, client *http.Client, o providerResourceScope, r api.Request, limit int64) ([]byte, *GreenhouseResponse, error) {
	requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	request, e := http.NewRequestWithContext(requestCtx, r.Method, r.URL, strings.NewReader(r.Body))
	if e != nil {
		return nil, nil, queue.ErrConfiguration
	}
	request.Header.Set("User-Agent", ordinaryUserAgent)
	request.Header.Set("Accept", ordinaryAccept)
	for key, values := range r.Headers {
		request.Header[key] = append([]string{}, values...)
	}
	response, e := client.Do(request)
	if e != nil {
		return nil, nil, &DiscoveryError{Kind: "request_failed"}
	}
	defer response.Body.Close()
	if response.Request == nil || response.Request.URL == nil || !o.ResourceMatches(response.Request.URL.String()) {
		return nil, nil, queue.ErrConfiguration
	}
	observed := &GreenhouseResponse{endpoint: r.URL, finalURL: response.Request.URL.String(), status: response.StatusCode, contentType: response.Header.Get("Content-Type"), location: response.Header.Get("Location")}
	reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
	signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
	check := func(source string) error {
		err := policy.Check(signals, source, observed.finalURL)
		var reserved *policy.Reservation
		if errors.As(err, &reserved) {
			observed.reserved = true
			observed.policy = reserved.PolicyURL
			observed.reservationSource = reserved.Source
		}
		return err
	}
	if e = check(""); e != nil {
		return nil, observed, e
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, limit+1))
	observed.bytes = len(raw)
	if int64(len(raw)) > limit {
		return nil, observed, &DiscoveryError{Kind: "body_limit"}
	}
	if e != nil {
		return nil, observed, &DiscoveryError{Kind: "body_failed"}
	}
	if ctx.Err() != nil {
		return nil, observed, ctx.Err()
	}
	if e = check(jsonld.DecodeDocument(raw, observed.contentType)); e != nil {
		return nil, observed, e
	}
	if response.StatusCode != 200 {
		return nil, observed, &DiscoveryError{Kind: "http_status", Status: response.StatusCode}
	}
	return raw, observed, nil
}

func FetchRemainingHTTPProviders(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.RemainingHTTPOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		if !o.ResourceMatches(r.URL) || r.Method != "GET" && r.Method != "POST" || (r.URL == api.JobDivaAPI+"/job/searchjobsportal") != (r.Method == "POST") || r.Method == "GET" && r.Body != "" {
			return nil, queue.ErrConfiguration
		}
		attempts := 3
		baseDelay := 500 * time.Millisecond
		if o.Provider == "headhunter" {
			attempts = 1
		}
		if o.Provider == "jobdiva" {
			baseDelay = time.Second
		}
		limit := int64(64 << 20)
		if o.Provider == "johdi" {
			limit = 2_000_000
			if r.URL == o.JohdiListURL() {
				limit = 8_000_000
			}
		}
		for attempt := 0; attempt < attempts; attempt++ {
			raw, observed, err := fetchRemainingHTTPResource(ctx, &sealed, o, r, limit)
			if observed != nil && (out.Response == nil || !out.Response.reserved) {
				out.Response = observed
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			var reservation *policy.Reservation
			if errors.As(err, &reservation) {
				return nil, err
			}
			status := 0
			if observed != nil {
				status = observed.status
				if !o.ResourceMatches(observed.finalURL) {
					return nil, queue.ErrConfiguration
				}
			}
			if err == nil {
				if o.Provider == "johdi" && r.URL == o.JohdiListURL() {
					contentType, _, parseErr := mime.ParseMediaType(observed.contentType)
					if parseErr != nil || contentType != "application/json" && !strings.HasSuffix(contentType, "+json") {
						return nil, api.ErrInventory
					}
				}
				if err == nil && (o.Provider == "jobdiva" || o.Provider == "headhunter" && r.URL != o.HeadHunterPublicURL()) {
					_, err = api.Decode(raw)
				}
				if err == nil && len(raw) > 0 {
					if o.Provider == "johdi" && r.URL == o.BoardURL || o.Provider == "headhunter" && r.URL == o.HeadHunterPublicURL() {
						return []byte(jsonld.DecodeDocument(raw, observed.contentType)), nil
					}
					return raw, nil
				}
				if err == nil {
					err = api.ErrInventory
				}
			}
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt+1 == attempts {
				return nil, &remainingHTTPStatusError{status: status, cause: err}
			}
			delay := time.Duration(float64(baseDelay) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if e := wait(ctx, delay); e != nil {
				return nil, e
			}
		}
		return nil, api.ErrInventory
	}
	if o.Provider == "headhunter" {
		fields, truncated, err := api.DiscoverHeadHunter(ctx, o, fetch)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		out.Truncated = truncated
		for _, f := range fields {
			job, e := secondaryRichJob(f)
			if e != nil || !o.JobMatches(job.URL) {
				return RichDiscovery{Response: out.Response}, queue.ErrConfiguration
			}
			out.Jobs = append(out.Jobs, job)
		}
	} else {
		var urls []string
		var err error
		if o.Provider == "johdi" {
			urls, out.Truncated, err = api.DiscoverJohdi(ctx, o, fetch)
		} else {
			urls, out.Truncated, err = api.DiscoverJobDiva(ctx, o, fetch)
		}
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		for _, source := range urls {
			if !o.JobMatches(source) {
				return RichDiscovery{Response: out.Response}, queue.ErrConfiguration
			}
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
		}
	}
	return out, nil
}
