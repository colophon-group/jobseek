package worker

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchFinalHTTPProvidersHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.FinalHTTPProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	var responseMu sync.Mutex
	observe := func(response *GreenhouseResponse) {
		responseMu.Lock()
		defer responseMu.Unlock()
		if out.Response == nil || !out.Response.reserved {
			out.Response = response
		}
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		if !o.ResourceMatches(r.URL) || (r.Method != "GET" && r.Method != "POST") || (o.Provider == "paynet" || o.Provider == "fenbi") && (r.Method != "GET" || r.Body != "") || o.Provider == "wecruit" && r.Method != "POST" || o.Provider == "nowhiring" && ((r.URL == "https://nowhiring.com/api/jobs/search") != (r.Method == "POST")) {
			return nil, queue.ErrConfiguration
		}
		attempts := 3
		if o.Provider == "nowhiring" || o.Provider == "fenbi" {
			attempts = 1
		}
		current, redirects := r.URL, 0
		method, requestBody, headers := r.Method, r.Body, r.Headers.Clone()
		for attempt := 0; attempt < attempts; {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			request, e := http.NewRequestWithContext(requestCtx, method, current, strings.NewReader(requestBody))
			if e != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			for key, v := range headers {
				request.Header[key] = append([]string{}, v...)
			}
			response, requestErr := sealed.Do(request)
			status := 0
			var body []byte
			var readErr error
			if requestErr == nil {
				if response.Request == nil || response.Request.URL == nil {
					response.Body.Close()
					cancel()
					return nil, queue.ErrConfiguration
				}
				final := response.Request.URL.String()
				if !o.ResourceMatches(final) {
					response.Body.Close()
					cancel()
					return nil, queue.ErrConfiguration
				}
				status = response.StatusCode
				observed := &GreenhouseResponse{endpoint: r.URL, finalURL: final, status: status, location: response.Header.Get("Location"), contentType: response.Header.Get("Content-Type")}
				reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
				check := func(source string) error {
					err := policy.Check(signals, source, final)
					var reserved *policy.Reservation
					if errors.As(err, &reserved) {
						observed.reserved = true
						observed.policy = reserved.PolicyURL
						observed.reservationSource = reserved.Source
					}
					return err
				}
				if e = check(""); e != nil {
					observe(observed)
					response.Body.Close()
					cancel()
					return nil, e
				}
				limit := int64(64 << 20)
				if o.Provider == "paynet" {
					limit = 50_000_000
				}
				if o.Provider == "wecruit" {
					limit = 10_000_000
				}
				body, readErr = io.ReadAll(io.LimitReader(response.Body, limit+1))
				response.Body.Close()
				observed.bytes = len(body)
				if int64(len(body)) > limit {
					cancel()
					return nil, &DiscoveryError{Kind: "body_limit"}
				}
				if e = check(jsonld.DecodeDocument(body, response.Header.Get("Content-Type"))); e != nil {
					observe(observed)
					cancel()
					return nil, e
				}
				observe(observed)
				cancel()
				if readErr == nil && (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
					base, _ := url.Parse(current)
					next, err := base.Parse(response.Header.Get("Location"))
					if err != nil || redirects >= 20 || !o.ResourceMatches(next.String()) {
						return nil, queue.ErrConfiguration
					}
					current = next.String()
					if status == 302 || status == 303 || status == 301 && method == "POST" {
						method, requestBody = "GET", ""
						headers.Del("Content-Type")
						headers.Del("Content-Length")
					}
					redirects++
					continue
				}
				if readErr == nil && (status == 200 || o.Provider == "paynet" && status == 201 || (o.Provider == "nowhiring" || o.Provider == "fenbi") && status >= 200 && status < 300) {
					if o.Provider == "fenbi" {
						return []byte(jsonld.DecodeDocument(body, response.Header.Get("Content-Type"))), nil
					}
					if o.Provider == "paynet" {
						// The original JSON-page helper retries parsing and shape errors.
						if d, err := api.Decode(body); err == nil {
							if _, ok := d.Value.([]any); ok {
								return body, nil
							}
						}
					} else if len(body) > 0 {
						return body, nil
					}
				}
			} else {
				cancel()
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			retry := status == 0 || status == 200 || o.Provider == "paynet" && status == 201 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599

			if !retry || attempt+1 == attempts {
				if requestErr != nil {
					return nil, &DiscoveryError{Kind: "request_failed"}
				}
				if readErr != nil {
					return nil, &DiscoveryError{Kind: "body_failed", cause: readErr}
				}
				kind := "json_page_failed"
				if o.Provider == "nowhiring" && r.URL == o.ListingURL() && (status == 404 || status == 410) {
					kind = "provider_gone"
				}
				return nil, &DiscoveryError{Kind: kind, Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if e = wait(ctx, delay); e != nil {
				return nil, e
			}
			attempt++
			current, redirects = r.URL, 0
			method, requestBody, headers = r.Method, r.Body, r.Headers.Clone()
		}
		return nil, api.ErrInventory
	}

	var fields []map[string]any
	var truncated bool
	switch o.Provider {
	case "paynet":
		fields, truncated, e = api.DiscoverPayNet(ctx, o.BoardURL, fetch)
	case "nowhiring":
		fields, truncated, e = api.DiscoverNowHiring(ctx, o.Tenant, fetch)
	case "fenbi":
		fields, truncated, e = api.DiscoverFenbi(ctx, o.BoardURL, o.Kind, fetch)
	case "wecruit":
		fields, truncated, e = api.DiscoverWecruit(ctx, o.Origin, o.Tenant, o.RecruitTypes, fetch)
	default:
		return out, queue.ErrConfiguration
	}
	if e != nil {
		return out, e
	}
	out.Truncated = truncated
	for _, field := range fields {
		job, err := secondaryRichJob(field)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		job.SourceIdentity, _ = field["source_identity"].(string)
		if !o.IdentityMatches(job.URL, job.SourceIdentity) {
			return RichDiscovery{Response: out.Response}, queue.ErrConfiguration
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
