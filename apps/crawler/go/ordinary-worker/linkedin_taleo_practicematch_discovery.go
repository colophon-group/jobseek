package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"time"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchLinkedInTaleoPracticeMatchHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	if client == nil || wait == nil || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	var linkedin api.LinkedInOptions
	var taleo api.TaleoOptions
	var practice api.PracticeMatchOptions
	var err error
	switch p.Provider {
	case "linkedin":
		linkedin, err = api.LinkedInOptionsFromMetadata(config["board_url"], config["metadata"])
		if err != nil || len(linkedin.CompanyIDs) == 0 || p.Profile != "linkedin.guest-items/v1" || p.Endpoint != api.LinkedInListingRequest(strings.Join(linkedin.CompanyIDs, ","), "", 0).URL {
			return out, queue.ErrConfiguration
		}
	case "taleo":
		taleo, err = api.TaleoOptionsFromMetadata(config["board_url"], config["metadata"])
		if err != nil || !taleo.Configured || p.Profile != "taleo.listing-urls/v1" || p.Endpoint != taleo.Board.ListingURL(nil) {
			return out, queue.ErrConfiguration
		}
	case "practicematch":
		practice, err = api.PracticeMatchOptionsFromMetadata(config["board_url"], config["metadata"])
		if err != nil || p.Profile != "practicematch.proxy-listing-urls/v1" || p.Endpoint != practice.BoardURL {
			return out, queue.ErrConfiguration
		}
	default:
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
		if !queue.SecondaryMonitorResourceMatches(p, config, r.URL) || r.Method != "GET" && (p.Provider != "practicematch" || r.Method != "POST" || r.URL != practice.Endpoint) || r.Method == "GET" && p.Provider == "practicematch" && r.URL != practice.BoardURL {
			return nil, queue.ErrConfiguration
		}
		current := r.URL
		redirects := 0
		attempts := 3
		baseDelay := 500 * time.Millisecond
		limit := int64(25_000_000)
		if p.Provider == "linkedin" {
			attempts = 4
			baseDelay = 1500 * time.Millisecond
		}
		if p.Provider == "taleo" {
			limit = 8_000_000
		}
		for attempt := 0; attempt < attempts; {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			request, e := http.NewRequestWithContext(requestCtx, r.Method, current, strings.NewReader(r.Body))
			if e != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			request.Header.Set("Accept", ordinaryAccept)
			for k, v := range r.Headers {
				request.Header[k] = append([]string{}, v...)
			}
			response, e := sealed.Do(request)
			status := 0
			var raw []byte
			readFailed := false
			if response != nil {
				if response.Request == nil || response.Request.URL == nil {
					response.Body.Close()
					cancel()
					return nil, queue.ErrConfiguration
				}
				observed := &GreenhouseResponse{endpoint: current, finalURL: response.Request.URL.String(), status: response.StatusCode, location: response.Header.Get("Location"), contentType: response.Header.Get("Content-Type")}
				out.Response = observed
				status = observed.status
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
				if e = check(""); e == nil {
					var readError error
					raw, readError = io.ReadAll(io.LimitReader(response.Body, limit+1))
					observed.bytes = len(raw)
					if int64(len(raw)) > limit {
						e = api.ErrInventory
					} else {
						e = check(jsonld.DecodeDocument(raw, observed.contentType))
						if e == nil && readError != nil {
							e, readFailed = readError, true
						}
					}
				}
				response.Body.Close()
				cancel()
				if e != nil && !readFailed {
					return nil, e
				}
				redirect := status == 301 || status == 302 || status == 303 || status == 307 || status == 308
				if p.Provider == "taleo" && r.URL == p.Endpoint && !readFailed && redirect {
					if api.TaleoInactiveRedirect(taleo.Board, current, observed.location) {
						observed.providerDisabled = true
						return nil, &DiscoveryError{Kind: "provider_gone", Status: status}
					}
					next, b, err := api.TaleoSafeRedirect(taleo.Board, current, observed.location)
					if err != nil || b != taleo.Board || redirects >= 4 {
						return nil, queue.ErrConfiguration
					}
					current = next
					redirects++
					continue
				}
				if p.Provider == "taleo" && r.URL == p.Endpoint && !readFailed && (status == 404 || status == 410) {
					return nil, &DiscoveryError{Kind: "provider_gone", Status: status}
				}
				if p.Provider == "linkedin" && !readFailed && (status == 404 || status == 410) {
					return nil, nil
				}
				if e == nil && status == 200 {
					decoded := jsonld.DecodeDocument(raw, observed.contentType)
					if p.Provider == "linkedin" || strings.TrimSpace(decoded) != "" {
						if p.Provider == "taleo" && utf8.RuneCountInString(decoded) > 2_000_000 {
							return nil, api.ErrInventory
						}
						if p.Provider == "taleo" {
							classification, err := dom.ClassifyDocument(decoded, dom.Object{}, observed.finalURL)
							if err != nil || classification["classification"] == "challenge" {
								return nil, api.ErrInventory
							}
						}
						return []byte(decoded), nil
					}
				}
			} else {
				cancel()
			}
			retry := readFailed || status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599 || p.Provider == "linkedin" && (status == 401 || status == 403 || status == 999) || p.Provider == "taleo" && (status == 202 || status == 401 || status == 403)
			if !retry || attempt == attempts-1 {
				if e != nil {
					return nil, e
				}
				return nil, &DiscoveryError{Kind: "provider_resource_failed", Status: status}
			}
			if err := wait(ctx, time.Duration(1<<attempt)*baseDelay); err != nil {
				return nil, err
			}
			attempt++
		}
		return nil, api.ErrInventory
	}
	var urls []string
	switch p.Provider {
	case "linkedin":
		inventory, e := api.DiscoverLinkedIn(ctx, linkedin, fetch, func(ctx context.Context) error { return wait(ctx, time.Second) })
		if e != nil {
			return out, e
		}
		out.Truncated = inventory.Truncated
		for _, j := range inventory.Jobs {
			job, e := secondaryRichJob(map[string]any{"url": j.URL, "title": j.Title, "locations": j.Locations, "date_posted": j.DatePosted, "metadata": j.Metadata})
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			out.Jobs = append(out.Jobs, job)
		}
		return out, ctx.Err()
	case "taleo":
		urls, err = api.DiscoverTaleo(ctx, taleo, func(ctx context.Context, u string) ([]byte, error) {
			return fetch(ctx, api.Request{Method: "GET", URL: u})
		})
	case "practicematch":
		inventory, e := api.DiscoverPracticeMatch(ctx, practice, fetch)
		urls, out.Truncated, err = inventory.URLs, inventory.Truncated, e
	}
	if err != nil {
		return out, err
	}
	for _, u := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: u, URLOnly: true})
	}
	return out, ctx.Err()
}
