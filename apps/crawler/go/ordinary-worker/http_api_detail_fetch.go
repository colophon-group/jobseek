package worker

import (
	"context"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"
)

func fetchHTTPAPIDetail(ctx context.Context, client *http.Client, profile queue.WorkdayDetailProfile) (map[string]any, *publisherpolicy.Reservation, error) {
	o, err := apisniffer.HTTPDetailOptionsForSource(profile.HTTPAPIConfig, profile.SourceURL)
	if err != nil || o.Request.URL != profile.Endpoint {
		return nil, nil, queue.ErrConfiguration
	}
	if o.Auth != nil {
		d, reserved, err := fetchHTTPDetailDocument(ctx, client, o.Auth.Request)
		if err != nil || reserved != nil {
			return nil, reserved, err
		}
		if d == nil {
			return nil, nil, &DiscoveryError{Kind: "auth_empty"}
		}
		headers, err := apisniffer.HTTPDetailAuthHeaders(d, o.Auth)
		if err != nil {
			return nil, nil, &DiscoveryError{Kind: "auth_fields"}
		}
		for k, v := range headers {
			o.Request.Headers[k] = v
		}
	}
	d, reserved, err := fetchHTTPDetailDocument(ctx, client, o.Request)
	if err != nil || reserved != nil {
		return nil, reserved, err
	}
	if d == nil {
		return nil, nil, executor.ErrEmptyResult
	}
	values, err := apisniffer.ProjectHTTPDetail(d, o)
	if err == nil {
		if text, ok := values["base_salary"].(string); ok {
			parsed, failure := enrichment.Salary(text, nil)
			if failure != nil {
				return nil, nil, failure
			}
			delete(values, "base_salary")
			if parsed.Parsed != nil {
				values["base_salary"] = parsed.Parsed
			}
		}
	}
	return values, nil, err
}

// Python's detail contract retries transport, JSON and retryable HTTP failures
// three times. Successful publisher headers stop the operation before parsing.
func fetchHTTPDetailDocument(ctx context.Context, client *http.Client, r apisniffer.Request) (*apisniffer.Document, *publisherpolicy.Reservation, error) {
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		var body io.Reader
		if r.Method == http.MethodPost && r.Body != "" {
			body = strings.NewReader(r.Body)
		}
		request, err := http.NewRequestWithContext(requestCtx, r.Method, r.URL, body)
		if err != nil {
			cancel()
			return nil, nil, queue.ErrConfiguration
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		for k, v := range r.Headers {
			request.Header[k] = append([]string{}, v...)
		}
		if body != nil && request.Header.Get("Content-Type") == "" {
			request.Header.Set("Content-Type", "application/json")
		}
		response, err := client.Do(request)
		last = &DiscoveryError{Kind: "request_failed"}
		if err == nil {
			if response.Request == nil || response.Request.URL == nil {
				response.Body.Close()
				cancel()
				return nil, nil, &DiscoveryError{Kind: "invalid_response"}
			}
			status := response.StatusCode
			if status >= 200 && status < 300 {
				reservation, policy := greenhouseHeaders(response.Header)
				if reservation == "1" {
					response.Body.Close()
					cancel()
					return nil, &publisherpolicy.Reservation{URL: response.Request.URL.String(), Source: "header", PolicyURL: policy}, nil
				}
				data, err := io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
				response.Body.Close()
				if len(data) > 64<<20 {
					cancel()
					return nil, nil, &DiscoveryError{Kind: "body_limit"}
				}
				last = &DiscoveryError{Kind: "body_failed"}
				if err == nil {
					d, err := apisniffer.Decode(data)
					if err == nil {
						cancel()
						return d, nil, nil
					}
					last = &DiscoveryError{Kind: "invalid_detail", Status: status}
				}
			} else {
				response.Body.Close()
				if status == 404 || status == 410 {
					cancel()
					return nil, nil, nil
				}
				last = &DiscoveryError{Kind: "http_status", Status: status}
				if status < 500 && status != 408 && status != 425 && status != 429 {
					cancel()
					return nil, nil, last
				}
			}
		}
		cancel()
		if attempt < 2 {
			if err := pauseRich(ctx, time.Duration(float64(time.Second)*float64(int64(1)<<attempt)*rand.Float64())); err != nil {
				return nil, nil, err
			}
		}
	}
	return nil, nil, last
}
