package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"time"
)

func discoverICIMSInventory(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := queue.ICIMSMonitorOptions(config)
	if err != nil || verified == nil || !queue.ICIMSMonitorResourceMatches(profile, config, profile.Endpoint) {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	inventory, err := dom.DiscoverICIMS(ctx, o, func(ctx context.Context, r dom.ICIMSRequest) ([]byte, error) {
		if !queue.ICIMSMonitorResourceMatches(profile, config, r.URL) {
			return nil, queue.ErrConfiguration
		}
		body, response, err := fetchICIMSPage(ctx, &client, r)
		result.Response = response
		return body, err
	})
	if err != nil {
		return result, err
	}
	result.Truncated = inventory.Truncated
	for _, source := range inventory.URLs {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: source})
	}
	return result, nil
}

// Classic listings never follow redirects. Jibe pages retain the sealed client's
// reviewed redirect policy and share a per-claim cookie jar with earlier pages.
func fetchICIMSPage(ctx context.Context, verified *http.Client, r dom.ICIMSRequest) ([]byte, *GreenhouseResponse, error) {
	client := *verified
	if !r.FollowRedirects {
		client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	}
	var last *GreenhouseResponse
	var failure error
	for attempt := 0; attempt < 3; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, last, err
		}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		request, err := http.NewRequestWithContext(requestCtx, http.MethodGet, r.URL, nil)
		if err != nil {
			cancel()
			return nil, last, queue.ErrConfiguration
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		response, err := client.Do(request)
		last = nil
		failure = &DiscoveryError{Kind: "request_failed"}
		retry := true
		var data []byte
		if err == nil {
			if response.Request == nil || response.Request.URL == nil {
				response.Body.Close()
				cancel()
				return nil, nil, queue.ErrConfiguration
			}
			last = &GreenhouseResponse{endpoint: r.URL, finalURL: response.Request.URL.String(), status: response.StatusCode}
			if response.StatusCode == 200 {
				reservation, policy := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policy}
				// Successful headers are authoritative before a streamed body/JSON parse.
				policyErr := publisherpolicy.Check(signals, "", last.finalURL)
				if policyErr == nil {
					limit := int64(64 << 20)
					if r.JSON {
						limit = 2_000_000
					}
					data, err = io.ReadAll(io.LimitReader(response.Body, limit+1))
					if len(data) > int(limit) {
						failure = &DiscoveryError{Kind: "body_limit"}
						retry = false
					} else if err == nil {
						text := jsonld.DecodeDocument(data, response.Header.Get("Content-Type"))
						policyErr = publisherpolicy.Check(signals, text, last.finalURL)
						if policyErr == nil {
							if r.JSON {
								var payload map[string]any
								d := json.NewDecoder(bytes.NewReader(data))
								d.UseNumber()
								if d.Decode(&payload) != nil || payload == nil || d.Decode(new(any)) != io.EOF {
									failure = &DiscoveryError{Kind: "invalid_json"}
								} else {
									failure = nil
								}
							} else if text != "" {
								count := 0
								for offset := range text {
									if count == 2_000_000 {
										text = text[:offset]
										break
									}
									count++
								}
								data = []byte(text)
								failure = nil
							} else {
								failure = &DiscoveryError{Kind: "empty_page"}
							}
						}
					} else {
						failure = &DiscoveryError{Kind: "body_failed"}
					}
				}
				if policyErr != nil {
					var reserved *publisherpolicy.Reservation
					if errors.As(policyErr, &reserved) {
						last.reserved = true
						last.policy = reserved.PolicyURL
						last.reservationSource = reserved.Source
					}
					response.Body.Close()
					cancel()
					return nil, last, policyErr
				}
			} else {
				status := response.StatusCode
				failure = &DiscoveryError{Kind: "http_status", Status: status}
				retry = status == 202 || status == 401 || status == 403 || status == 408 || status == 425 || status == 429 || status >= 500
			}
			response.Body.Close()
		}
		cancel()
		if err := ctx.Err(); err != nil {
			return nil, last, err
		}
		if failure == nil {
			return data, last, nil
		}
		if !retry || attempt == 2 {
			return nil, last, failure
		}
		if err := pauseRich(ctx, time.Duration(float64(500*time.Millisecond)*float64(int64(1)<<attempt)*(0.5+rand.Float64()))); err != nil {
			return nil, last, err
		}
	}
	return nil, last, failure
}
