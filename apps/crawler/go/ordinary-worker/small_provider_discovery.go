package worker

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
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

func FetchSmallProvidersHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.SmallProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
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
		if r.Method != "GET" || !o.ResourceMatches(r.URL) {
			return nil, queue.ErrConfiguration
		}
		attempts := 3
		if o.Provider == "seamlesshiring" || o.Provider == "jarvi" {
			attempts = 1
		}
		current, redirects := r.URL, 0
		for attempt := 0; attempt < attempts; {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			request, e := http.NewRequestWithContext(requestCtx, "GET", current, nil)
			if e != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			for key, v := range r.Headers {
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
				body, readErr = io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
				response.Body.Close()
				observed.bytes = len(body)
				if len(body) > 64<<20 {
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
					redirects++
					continue
				}
				if readErr == nil && (status == 200 || o.Provider == "seamlesshiring" && status >= 200 && status < 300) {
					// Python retries transport/empty text, then parses JSONP once.
					if o.Provider == "job51" && len(body) != 0 {
						text := jsonld.DecodeDocument(body, response.Header.Get("Content-Type"))
						if utf8.RuneCountInString(text) > 5_000_000 {
							text = string([]rune(text)[:5_000_000])
						}
						return []byte(text), nil
					}
					d, err := api.Decode(body)
					if err == nil {
						if _, ok := d.Value.(map[string]any); ok {
							return body, nil
						}
					}
					if o.Provider == "seamlesshiring" {
						return nil, api.ErrInventory
					}
				}
			} else {
				cancel()
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if o.Provider == "jobbank104" && (status == 202 || status == 401 || status == 403 || status == 999) {
				retry = true
			}
			if o.Provider == "seamlesshiring" || !retry || attempt+1 == attempts {
				if requestErr != nil {
					return nil, &DiscoveryError{Kind: "request_failed"}
				}
				if readErr != nil {
					return nil, &DiscoveryError{Kind: "body_failed", cause: readErr}
				}
				kind := "json_page_failed"
				if o.Provider != "seamlesshiring" && o.Provider != "jarvi" && (status == 404 || status == 410) {
					kind = "provider_gone"
				}
				return nil, &DiscoveryError{Kind: kind, Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if e = wait(ctx, delay); e != nil {
				return nil, e
			}
			attempt++
		}
		return nil, api.ErrInventory
	}
	fields, truncated, e := api.DiscoverSmallProvider(ctx, o, fetch, enrichment.NormalizeDescriptionHTML)
	if e != nil {
		return out, e
	}
	out.Truncated = truncated
	for _, field := range fields {
		if o.Provider == "seamlesshiring" {
			value, _ := field["job_location_type"].(string)
			field["job_location_type"] = nil
			if value = enrichment.NormalizeJobLocationType(value); value != "" {
				field["job_location_type"] = value
			}
		}
		job, err := secondaryRichJob(field)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		if o.Provider == "job51" {
			job.SourceIdentity, _ = field["source_identity"].(string)
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
