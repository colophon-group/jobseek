package worker

import (
	"bytes"
	"context"
	"errors"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"net/http"
	"time"
)

// Each provider supplies its exact source-bound resources and existing body cap.
// Retry and optional-fallback decisions stay in that provider's discovery.
type providerResourceScope interface{ ResourceMatches(string) bool }

func fetchProviderResource(ctx context.Context, client *http.Client, options providerResourceScope, endpoint string, body []byte, headers http.Header, limit int64) ([]byte, *GreenhouseResponse, error) {
	return fetchProviderStatusResource(ctx, client, options, endpoint, body, headers, limit, nil)
}

// PCSX's 403 JSON distinguishes disabled tenants from transient failures.
// Only that caller opts in to a bounded status body; other providers retain
// their existing early failure behavior.
func fetchProviderStatusResource(ctx context.Context, client *http.Client, options providerResourceScope, endpoint string, body []byte, headers http.Header, limit int64, inspectStatus map[int]bool, any2xxGET ...bool) ([]byte, *GreenhouseResponse, error) {
	if client == nil || !options.ResourceMatches(endpoint) || limit < 1 || limit > 64<<20 {
		return nil, nil, queue.ErrConfiguration
	}
	requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	method := http.MethodGet
	if body != nil {
		method = http.MethodPost
	}
	request, e := http.NewRequestWithContext(requestCtx, method, endpoint, bytes.NewReader(body))
	if e != nil {
		return nil, nil, queue.ErrConfiguration
	}
	request.Header.Set("User-Agent", ordinaryUserAgent)
	request.Header.Set("Accept", ordinaryAccept)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		request.Header[k] = append([]string{}, v...)
	}
	response, e := client.Do(request)
	if e != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		return nil, nil, &DiscoveryError{Kind: "request_failed"}
	}
	defer response.Body.Close()
	if response.Request == nil || response.Request.URL == nil {
		return nil, nil, queue.ErrConfiguration
	}
	observed := &GreenhouseResponse{endpoint: endpoint, finalURL: response.Request.URL.String(), status: response.StatusCode, location: response.Header.Get("Location")}
	var statusError error
	acceptAnyGET := len(any2xxGET) == 1 && any2xxGET[0]
	if response.StatusCode < 200 || response.StatusCode >= 300 || body == nil && response.StatusCode != 200 && !acceptAnyGET {
		kind := "http_status"
		if body == nil && response.StatusCode == 404 {
			kind = "provider_gone"
		}
		statusError = &DiscoveryError{Kind: kind, Status: response.StatusCode}
		if !inspectStatus[response.StatusCode] {
			return nil, observed, statusError
		}
	}
	reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
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
	if statusError == nil {
		if e := check(""); e != nil {
			return nil, observed, e
		}
	}
	if scoped, ok := options.(interface{ HonorContentLength() bool }); ok && scoped.HonorContentLength() && response.ContentLength > limit {
		return nil, observed, &DiscoveryError{Kind: "body_limit"}
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, limit+1))
	observed.bytes = len(raw)
	if int64(len(raw)) > limit {
		return nil, observed, &DiscoveryError{Kind: "body_limit"}
	}
	if e != nil {
		return nil, observed, &DiscoveryError{Kind: "body_failed", cause: e}
	}
	if ctx.Err() != nil {
		return nil, observed, ctx.Err()
	}
	if statusError != nil {
		return raw, observed, statusError
	}
	if e := check(jsonld.DecodeDocument(raw, response.Header.Get("Content-Type"))); e != nil {
		return nil, observed, e
	}
	return raw, observed, nil
}
