package worker

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
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

// Follow the existing public redirect contract while checking publisher signals
// on each response, including redirects, status failures and incomplete bodies.
func staticProviderDetailPage(ctx context.Context, client *http.Client, endpoint string, limit int64, singleRequest ...api.Request) ([]byte, int, string, *policy.Reservation, error) {
	if len(singleRequest) > 1 || len(singleRequest) == 1 && (singleRequest[0].URL != endpoint || singleRequest[0].Method != "GET" || singleRequest[0].Body != "") {
		return nil, 0, endpoint, nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	current := endpoint
	for hop := 0; hop <= 20; hop++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, current, nil)
		if err != nil {
			return nil, 0, current, nil, queue.ErrConfiguration
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		if len(singleRequest) == 1 {
			for key, value := range singleRequest[0].Headers {
				request.Header[key] = append([]string{}, value...)
			}
		}
		response, failure := sealed.Do(request)
		if response == nil {
			return nil, 0, current, nil, failure
		}
		if response.Request == nil || response.Request.URL == nil || response.Request.URL.String() != current {
			response.Body.Close()
			return nil, 0, current, nil, queue.ErrConfiguration
		}
		status := response.StatusCode
		reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
		signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
		failure = policy.Check(signals, "", current)
		var raw []byte
		if failure == nil {
			var readError error
			raw, readError = io.ReadAll(io.LimitReader(response.Body, limit+1))
			failure = policy.Check(signals, jsonld.DecodeDocument(raw, response.Header.Get("Content-Type")), current)
			if failure == nil {
				failure = readError
			}
		}
		response.Body.Close()
		var reserved *policy.Reservation
		if errors.As(failure, &reserved) {
			return nil, status, current, reserved, nil
		}
		if int64(len(raw)) > limit {
			return nil, status, current, nil, &DiscoveryError{Kind: "body_limit"}
		}
		if failure != nil {
			return nil, status, current, nil, failure
		}
		if status == 301 || status == 302 || status == 303 || status == 307 || status == 308 {
			if len(singleRequest) == 1 {
				return raw, status, current, nil, nil
			}
			location := response.Header.Get("Location")
			if location == "" {
				return raw, status, current, nil, nil
			}
			target, err := request.URL.Parse(location)
			if err != nil || target.User != nil || target.Host == "" || target.Scheme != "https" && target.Scheme != "http" || hop == 20 {
				return nil, status, current, nil, &DiscoveryError{Kind: "redirect"}
			}
			current = target.String()
			continue
		}
		// Normalize the actual response charset before parsing, as response.text does.
		return []byte(jsonld.DecodeDocument(raw, response.Header.Get("Content-Type"))), status, current, nil, nil
	}
	return nil, 0, current, nil, &DiscoveryError{Kind: "redirect"}
}

func fetchJobConvoDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	request, id, err := api.JobConvoDetailRequest(p.SourceURL, p.APILocale)
	if err != nil || client == nil || p.Profile != "jobconvo.public-detail/v1" || p.Endpoint != request.URL {
		return nil, nil, queue.ErrConfiguration
	}
	requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	raw, status, _, reserved, err := staticProviderDetailPage(requestCtx, client, p.Endpoint, 25_000_000, request)
	if reserved != nil {
		return nil, reserved, nil
	}
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	if err != nil {
		return nil, nil, err
	}
	if status != 200 {
		return nil, nil, executor.ErrEmptyResult
	}
	d, err := api.Decode(raw)
	if err != nil {
		return nil, nil, err
	}
	row, ok := d.Value.(map[string]any)
	if !ok {
		return nil, nil, executor.ErrEmptyResult
	}
	observed, _ := row["id"].(string)
	if strings.ToLower(observed) != id {
		return nil, nil, executor.ErrEmptyResult
	}
	fields := api.JobConvoDetailFields(row)
	// Original JobContent normalizes a textual salary on construction.
	if text, ok := row["salary"].(string); ok {
		salary, err := enrichment.Salary(text, nil)
		if err != nil {
			return nil, nil, err
		}
		delete(fields, "base_salary")
		if salary != nil && salary.Parsed != nil {
			fields["base_salary"] = salary.Parsed
		}
	}
	return fields, nil, nil
}

func fetchStaticProviderDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	provider := map[string]string{"linkedin.guest-detail/v1": "linkedin", "jazzhr.public-detail/v1": "jazzhr", "taleo.enterprise-detail/v1": "taleo"}[p.Profile]
	o, err := api.StaticProviderDetailOptionsForSource(provider, p.SourceURL)
	if err != nil || client == nil || wait == nil || p.Endpoint != o.Endpoint {
		return nil, nil, queue.ErrConfiguration
	}
	used := map[int]int{}
	retries := 0
	for {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		limit := int64(25_000_000)
		if provider == "taleo" {
			limit = 8 << 20
		}
		raw, status, final, reserved, failure := staticProviderDetailPage(requestCtx, client, o.Endpoint, limit)
		cancel()
		if reserved != nil {
			return nil, reserved, nil
		}
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		if failure == nil {
			if status == 404 || status == 410 {
				return nil, nil, &executor.NavigationHTTPError{RequestedURL: p.SourceURL, ResponseURL: final, Status: uint32(status)}
			}
			if (provider == "linkedin" && status == 200) || (provider != "linkedin" && status >= 200 && status < 300) {
				switch provider {
				case "linkedin":
					values, err := api.ParseLinkedInDetail(string(raw))
					return values, nil, err
				case "jazzhr":
					values, err := api.ParseJazzHRDetail(string(raw))
					return values, nil, err
				default:
					values, err := api.ParseTaleoEnterpriseDetail(string(raw))
					return values, nil, err
				}
			}
			failure = &DiscoveryError{Kind: "http_status", Status: status}
		}
		retry := false
		base := 500 * time.Millisecond
		if provider == "linkedin" {
			base = 1500 * time.Millisecond
			var statusFailure *DiscoveryError
			isStatusFailure := errors.As(failure, &statusFailure) && statusFailure.Kind == "http_status"
			retry = retries < 3 && (!isStatusFailure || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status < 600)
			var bounded *DiscoveryError
			if errors.As(failure, &bounded) && (bounded.Kind == "body_limit" || bounded.Kind == "redirect") {
				retry = false
			}
		} else {
			// Transport/body failures are not status retries in the Python helper.
			var statusFailure *DiscoveryError
			if errors.As(failure, &statusFailure) && statusFailure.Kind == "http_status" {
				budget := 0
				if provider == "jazzhr" && status == 403 {
					budget = 1
				}
				if provider == "taleo" && (status == 429 || status == 503) {
					budget = 2
				}
				retry = used[status] < budget
			}
		}
		if !retry {
			return nil, nil, failure
		}
		used[status]++
		delay := time.Duration(float64(base*time.Duration(1<<retries)) * (0.5 + rand.Float64()))
		retries++
		if err := wait(ctx, delay); err != nil {
			return nil, nil, err
		}
	}
}
