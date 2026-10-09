package worker

import (
	"context"
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
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchPortalHTTPProviders(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error, emit func([]RichMonitorJob) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.PortalHTTPProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Profile != o.Profile() || p.Endpoint != o.Listing {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if o.Provider == "infoniqa" {
		sealed.Jar, err = cookiejar.New(nil)
		if err != nil {
			return out, err
		}
	}
	var mu sync.Mutex
	observe := func(response *GreenhouseResponse) {
		mu.Lock()
		defer mu.Unlock()
		if out.Response == nil || !out.Response.reserved {
			out.Response = response
		}
	}
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		if !o.ResourceMatches(r.URL) || r.Method != "GET" && r.Method != "POST" || (o.Provider == "keka" || o.Provider == "pageup") && (r.Method != "GET" || r.Body != "") || o.Provider == "infoniqa" && ((r.URL == o.BoardURL) != (r.Method == "GET")) || o.Provider == "turbohire" && (strings.Contains(r.URL, "/filteredjobs?") != (r.Method == "POST")) {
			return nil, queue.ErrConfiguration
		}
		for attempt := 0; attempt < 3; attempt++ {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			request, err := http.NewRequestWithContext(requestCtx, r.Method, r.URL, strings.NewReader(r.Body))
			if err != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header = r.Headers.Clone()
			if request.Header == nil {
				request.Header = http.Header{}
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			if o.Provider == "turbohire" && r.Method == "POST" {
				request.Header.Set("Content-Type", "application/json")
			}
			response, requestErr := sealed.Do(request)
			status := 0
			var observed *GreenhouseResponse
			if requestErr == nil {
				if response.Request == nil || response.Request.URL == nil || !o.ResourceMatches(response.Request.URL.String()) {
					response.Body.Close()
					cancel()
					return nil, queue.ErrConfiguration
				}
				status = response.StatusCode
				observed = &GreenhouseResponse{endpoint: r.URL, finalURL: response.Request.URL.String(), status: status, location: response.Header.Get("Location"), contentType: response.Header.Get("Content-Type")}
				reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
				check := func(body string) error {
					err := policy.Check(signals, body, observed.finalURL)
					var reserved *policy.Reservation
					if errors.As(err, &reserved) {
						observed.reserved, observed.policy, observed.reservationSource = true, reserved.PolicyURL, reserved.Source
					}
					return err
				}
				if err := check(""); err != nil {
					observe(observed)
					response.Body.Close()
					cancel()
					return nil, err
				}
				limit := int64(64 << 20)
				if o.Provider == "infoniqa" {
					limit = 5_000_000
				} else if o.Provider == "pageup" {
					limit = 20_000_004
				} else if o.Provider == "keka" {
					limit = 100_000_004
					if r.URL == o.Keka.ListingURL() {
						limit = 2_000_004
					}
				}
				raw, readErr := io.ReadAll(io.LimitReader(response.Body, limit+1))
				response.Body.Close()
				observed.bytes = len(raw)
				if int64(len(raw)) > limit {
					cancel()
					return nil, &DiscoveryError{Kind: "body_limit"}
				}
				text := jsonld.DecodeDocument(raw, observed.contentType)
				if err := check(text); err != nil {
					observe(observed)
					cancel()
					return nil, err
				}
				observe(observed)
				cancel()
				if readErr == nil && status == 200 {
					if o.Provider == "turbohire" {
						if d, err := api.Decode(raw); err == nil {
							if _, ok := d.Value.(map[string]any); ok {
								return raw, nil
							}
						}
					} else if strings.TrimSpace(text) != "" {
						if o.Provider == "infoniqa" {
							expected := "text/html"
							if strings.HasPrefix(r.Body, "hasNextJobOffers=") {
								expected = "application/json"
							}
							if strings.ToLower(strings.TrimSpace(strings.Split(observed.contentType, ";")[0])) != expected {
								return nil, api.ErrInventory
							}
						}
						if o.Provider == "pageup" || o.Provider == "keka" && r.URL == o.Keka.ListingURL() {
							classified, err := dom.ClassifyDocument(text, dom.Object{}, r.URL)
							if err != nil || classified["classification"] == "challenge" {
								return nil, &DiscoveryError{Kind: "bot_challenge"}
							}
						}
						maxChars := 25_000_000
						if o.Provider == "pageup" {
							maxChars = 5_000_000
						} else if o.Provider == "keka" && r.URL == o.Keka.ListingURL() {
							maxChars = 500_000
						}
						if o.Provider != "infoniqa" && utf8.RuneCountInString(text) > maxChars {
							return nil, &DiscoveryError{Kind: "body_limit"}
						}
						return []byte(text), nil
					}
				}
			} else {
				cancel()
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if o.Provider != "infoniqa" && (status == 401 || status == 403 || o.Provider != "turbohire" && status == 202) {
				retry = true
			}
			if !retry || attempt == 2 {
				kind := "provider_fetch_failed"
				if o.Provider == "pageup" && r.URL == o.Listing && (status == 404 || status == 410) {
					kind = "provider_gone"
				}
				if o.Provider == "keka" && r.URL == o.Keka.ListingURL() {
					gone := status == 404 || status == 410
					if observed != nil && (status == 301 || status == 302 || status == 307 || status == 308) {
						base, _ := url.Parse(r.URL)
						next, e := base.Parse(observed.location)
						gone = e == nil && observed.location != "" && next.Scheme == "https" && strings.EqualFold(next.Hostname(), o.Keka.Tenant+".keka.com") && next.User == nil && (next.Port() == "" || next.Port() == "443") && strings.EqualFold(next.Path, "/careers/content/403.html") && next.RawQuery == "" && next.Fragment == ""
						observed.providerDisabled = gone
					}
					if gone {
						kind = "provider_gone"
					}
				}
				return nil, &DiscoveryError{Kind: kind, Status: status}
			}
			if err := wait(ctx, time.Duration(float64(500*time.Millisecond)*float64(uint64(1)<<uint(attempt))*(0.5+rand.Float64()))); err != nil {
				return nil, err
			}
		}
		return nil, api.ErrInventory
	}
	convert := func(fields []map[string]any) ([]RichMonitorJob, error) {
		jobs := []RichMonitorJob{}
		for _, field := range fields {
			if o.Provider == "infoniqa" {
				identity, _ := field["url"].(string)
				jobs = append(jobs, RichMonitorJob{URL: identity, URLOnly: true})
				continue
			}
			job, err := secondaryRichJob(field)
			if err != nil {
				return nil, err
			}
			job.Hybrid = o.Provider == "pageup"
			jobs = append(jobs, job)
		}
		return jobs, nil
	}
	var fields []map[string]any
	switch o.Provider {
	case "pageup":
		var page func([]map[string]any) error
		if emit != nil {
			page = func(fields []map[string]any) error {
				jobs, err := convert(fields)
				if err != nil {
					return err
				}
				return emit(jobs)
			}
		}
		fields, err = api.DiscoverPageUp(ctx, o, fetch, page)
	case "keka":
		fields, err = api.DiscoverKeka(ctx, o, fetch, enrichment.NormalizeDescriptionHTML)
	case "infoniqa":
		fields, err = api.DiscoverInfoniqa(ctx, o, fetch)
	case "turbohire":
		fields, err = api.DiscoverTurboHire(ctx, o, fetch, enrichment.NormalizeDescriptionHTML)
	}
	if err != nil {
		return out, err
	}
	out.Jobs, err = convert(fields)
	return out, err
}
