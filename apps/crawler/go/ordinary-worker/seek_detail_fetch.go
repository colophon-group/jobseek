package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func fetchSeekDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	o, err := api.SeekDetailOptionsForSource(p.SourceURL, p.HTTPAPIConfig)
	if client == nil || wait == nil || err != nil || p.Profile != "seek.graphql-detail/v1" || o.Request.URL != p.Endpoint {
		return nil, nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		request, err := http.NewRequestWithContext(requestCtx, o.Request.Method, o.Request.URL, strings.NewReader(o.Request.Body))
		if err != nil {
			cancel()
			return nil, nil, queue.ErrConfiguration
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		for k, v := range o.Request.Headers {
			request.Header[k] = append([]string{}, v...)
		}
		response, failure := sealed.Do(request)
		status := 0
		if response != nil {
			if response.Request == nil || response.Request.URL == nil || response.Request.URL.String() != p.Endpoint {
				response.Body.Close()
				cancel()
				return nil, nil, queue.ErrConfiguration
			}
			status = response.StatusCode
			reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
			signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
			check := func(body string) error { return policy.Check(signals, body, p.Endpoint) }
			failure = check("")
			var raw []byte
			if failure == nil {
				var readError error
				raw, readError = io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
				failure = check(jsonld.DecodeDocument(raw, response.Header.Get("Content-Type")))
				if failure == nil {
					failure = readError
				}
			}
			response.Body.Close()
			cancel()
			var reserved *policy.Reservation
			if errors.As(failure, &reserved) {
				return nil, reserved, nil
			}
			if len(raw) > 64<<20 {
				return nil, nil, &DiscoveryError{Kind: "body_limit"}
			}
			if failure == nil {
				if status == 404 || status == 410 {
					return nil, nil, executor.ErrEmptyResult
				}
				if status >= 200 && status < 300 {
					doc, decodeError := api.Decode(raw)
					if decodeError == nil {
						values, projectError := api.ProjectSeekDetail(doc, o)
						if projectError == nil && len(values) == 0 {
							projectError = executor.ErrEmptyResult
						}
						return values, nil, projectError
					}
					failure = decodeError
				} else {
					failure = &DiscoveryError{Kind: "http_status", Status: status}
					if status < 500 && status != 408 && status != 425 && status != 429 {
						return nil, nil, failure
					}
				}
			}
		} else {
			cancel()
		}
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		if attempt == 2 {
			return nil, nil, failure
		}
		if err := wait(ctx, time.Duration(1<<attempt)*500*time.Millisecond); err != nil {
			return nil, nil, err
		}
	}
	return nil, nil, api.ErrInventory
}
