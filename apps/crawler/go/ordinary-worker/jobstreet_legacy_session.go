package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/cookiejar"
	"regexp"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var jobStreetExplicitUSD = regexp.MustCompile(`(?i)(?:\bUSD\b|US\$)`)

func jobStreetSalary(job api.JobStreetJob, host string) (map[string]any, error) {
	if len(job.Fields) == 0 || job.SalaryLabel == "" {
		return job.Fields, nil
	}
	result, e := enrichment.Salary(job.SalaryLabel, nil)
	if e != nil {
		return nil, e
	}
	if result.Parsed != nil {
		salary := *result.Parsed
		if host == "sg.jobstreet.com" && salary.Currency == "USD" && strings.Contains(job.SalaryLabel, "$") && !jobStreetExplicitUSD.MatchString(job.SalaryLabel) {
			salary.Currency = "SGD"
		}
		job.Fields["base_salary"] = map[string]any{"currency": salary.Currency, "min": salary.Min, "max": salary.Max, "unit": salary.Unit}
	}
	return job.Fields, nil
}

// The two providers use fixed anonymous endpoints. Their session headers and
// cookies stay within this operation and never become configuration or logs.
func fetchJobStreetLegacyResource(ctx context.Context, client *http.Client, scope providerResourceScope, r api.Request, legacy bool, wait func(context.Context, time.Duration) error) ([]byte, http.Header, *GreenhouseResponse, error) {
	if client == nil || wait == nil || !scope.ResourceMatches(r.URL) || r.Method != "GET" && r.Method != "POST" || r.Method == "GET" && r.Body != "" {
		return nil, nil, nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	attempts := 3
	if !legacy && r.Method == "POST" {
		attempts = 1
	}
	limit := int64(64 << 20)
	if legacy {
		limit = 5000000
	}
	var observed *GreenhouseResponse
	var last error
	for attempt := 0; attempt < attempts; attempt++ {
		if ctx.Err() != nil {
			return nil, nil, observed, ctx.Err()
		}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		req, e := http.NewRequestWithContext(requestCtx, r.Method, r.URL, strings.NewReader(r.Body))
		if e != nil {
			cancel()
			return nil, nil, observed, queue.ErrConfiguration
		}
		req.Header.Set("User-Agent", ordinaryUserAgent)
		for k, v := range r.Headers {
			req.Header[k] = append([]string{}, v...)
		}
		response, e := sealed.Do(req)
		status := 0
		var raw []byte
		var headers http.Header
		if response != nil {
			if response.Request == nil || response.Request.URL == nil || response.Request.URL.String() != r.URL {
				response.Body.Close()
				cancel()
				return nil, nil, observed, queue.ErrConfiguration
			}
			status = response.StatusCode
			headers = response.Header.Clone()
			observed = &GreenhouseResponse{endpoint: r.URL, finalURL: r.URL, status: status, location: headers.Get("Location"), contentType: headers.Get("Content-Type")}
			reservation, policyURL := headers.Get("TDM-Reservation"), headers.Get("TDM-Policy")
			signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
			check := func(body string) error { return policy.Check(signals, body, r.URL) }
			e = check("")
			if e == nil {
				raw, last = io.ReadAll(io.LimitReader(response.Body, limit+1))
				e = check(jsonld.DecodeDocument(raw, headers.Get("Content-Type")))
				if e == nil {
					e = last
				}
			}
			response.Body.Close()
			cancel()
			observed.bytes = len(raw)
			var reserved *policy.Reservation
			if errors.As(e, &reserved) {
				observed.reserved = true
				observed.policy = reserved.PolicyURL
				observed.reservationSource = reserved.Source
				return nil, nil, observed, e
			}
			if int64(len(raw)) > limit {
				return nil, nil, observed, &DiscoveryError{Kind: "body_limit"}
			}
			if e == nil && status >= 200 && status < 300 {
				if legacy && len(raw) == 0 {
					e = api.ErrInventory
				} else if !legacy && r.Method == "GET" {
					doc, failure := api.Decode(raw)
					if failure == nil {
						if _, ok := doc.Value.(map[string]any); !ok {
							failure = api.ErrInventory
						}
					}
					e = failure
				}
				if e == nil {
					return raw, headers, observed, nil
				}
			}
			if e == nil {
				e = &DiscoveryError{Kind: "http_status", Status: status}
			}
		} else {
			cancel()
		}
		last = e
		if ctx.Err() != nil {
			return nil, nil, observed, ctx.Err()
		}
		retry := status == 0 || status >= 500 && status <= 599 || status == 408 || status == 425 || status == 429 || !legacy && status == 202 || status == 403 || legacy && status == 401 || status >= 200 && status < 300
		if !retry || attempt+1 == attempts {
			return nil, nil, observed, last
		}
		if e := wait(ctx, time.Duration(1<<attempt)*500*time.Millisecond); e != nil {
			return nil, nil, observed, e
		}
	}
	return nil, nil, observed, last
}
func FetchJobStreetHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.JobStreetOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || o.OrganisationID == "" || p.Profile != "jobstreet.company-items/v1" || p.Provider != "jobstreet" || p.Endpoint != o.PageRequest(1).URL {
		return out, queue.ErrConfiguration
	}
	inventory, e := api.DiscoverJobStreet(ctx, o, func(ctx context.Context, r api.Request) ([]byte, error) {
		if r.Method != "GET" || r.URL == o.CompanyRequest().URL {
			return nil, queue.ErrConfiguration
		}
		body, _, response, e := fetchJobStreetLegacyResource(ctx, client, o, r, false, wait)
		out.Response = response
		return body, e
	})
	if e != nil {
		return out, e
	}
	out.Truncated = inventory.Truncated
	for _, item := range inventory.Jobs {
		fields, e := jobStreetSalary(item, o.Host)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		job, e := secondaryRichJob(fields)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, nil
}
func FetchSuccessFactorsLegacyHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.SuccessFactorsLegacyOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || p.Profile != "rss.successfactors-legacy-session-items/v1" || p.Provider != "rss" || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar, e = cookiejar.New(nil)
	if e != nil {
		return out, e
	}
	inventory, e := api.DiscoverSuccessFactorsLegacy(ctx, o, func(ctx context.Context, r api.Request) ([]byte, http.Header, error) {
		if (r.URL == o.ListingURL()) != (r.Method == "GET") {
			return nil, nil, queue.ErrConfiguration
		}
		raw, headers, response, e := fetchJobStreetLegacyResource(ctx, &sealed, o, r, true, wait)
		out.Response = response
		return raw, headers, e
	}, func(fields []map[string]any) error {
		jobs := []RichMonitorJob{}
		for _, field := range fields {
			job, e := secondaryRichJob(field)
			if e != nil {
				return e
			}
			job.Hybrid = true
			jobs = append(jobs, job)
		}
		out.Jobs = append(out.Jobs, jobs...)
		return nil
	})
	if e != nil {
		if len(out.Jobs) > 0 {
			return out, &rssStreamPrefixError{cause: e}
		}
		return out, e
	}
	out.Truncated = inventory.Truncated
	return out, nil
}
func fetchJobStreetDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	r, host, id, e := api.JobStreetDetailRequest(p.SourceURL, p.HTTPAPIConfig)
	if e != nil || p.Profile != "jobstreet.graphql-detail/v1" || p.Endpoint != r.URL {
		return nil, nil, queue.ErrConfiguration
	}
	o := api.JobStreetOptions{Host: host}
	body, _, _, e := fetchJobStreetLegacyResource(ctx, client, o, r, false, wait)
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		return nil, nil, e
	}
	job, e := api.ParseJobStreetDetail(body, host, id)
	if e != nil {
		return nil, nil, e
	}
	if len(job.Fields) == 0 {
		return nil, nil, executor.ErrEmptyResult
	}
	fields, e := jobStreetSalary(job, host)
	return fields, nil, e
}
