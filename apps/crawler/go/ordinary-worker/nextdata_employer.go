package worker

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"sort"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func nextdataEmployerWitness(ctx context.Context, client *http.Client, url, expected string) (bool, *GreenhouseResponse, error) {
	var observation *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		r, err := http.NewRequestWithContext(requestCtx, "GET", url, nil)
		if err != nil {
			cancel()
			return false, nil, err
		}
		r.Header.Set("User-Agent", ordinaryUserAgent)
		r.Header.Set("Accept", ordinaryAccept)
		response, err := client.Do(r)
		if err == nil {
			if response.Request == nil || response.Request.URL == nil {
				response.Body.Close()
				cancel()
				return false, nil, errors.New("employer witness response lacks origin")
			}
			observation = &GreenhouseResponse{endpoint: url, finalURL: response.Request.URL.String(), status: response.StatusCode}
			if response.StatusCode == 200 {
				body, readErr := io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
				observation.bytes = len(body)
				if len(body) > 64<<20 {
					response.Body.Close()
					cancel()
					return false, observation, &DiscoveryError{Kind: "body_limit"}
				}
				if readErr == nil && len(body) > 0 {
					source := jsonld.DecodeDocument(body, response.Header.Get("Content-Type"))
					reservation, policyURL := greenhouseHeaders(response.Header)
					policyValue := ""
					if policyURL != nil {
						policyValue = *policyURL
					}
					policyErr := policy.Check(&runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyValue}, source, observation.finalURL)
					if policyErr != nil {
						var reserved *policy.Reservation
						if errors.As(policyErr, &reserved) {
							observation.reserved = true
							observation.policy = reserved.PolicyURL
							observation.reservationSource = reserved.Source
						}
						response.Body.Close()
						cancel()
						return false, observation, policyErr
					}
					classification, err := dom.ClassifyRendered(source, map[string]any{}, observation.finalURL)
					response.Body.Close()
					cancel()
					if err != nil || classification["classification"] == "challenge" {
						return false, observation, &DiscoveryError{Kind: "bot_challenge"}
					}
					return jsonld.ContainsJobPosting([]byte(source), expected), observation, nil
				}
			} else if response.StatusCode != 403 && response.StatusCode != 408 && response.StatusCode != 425 && response.StatusCode != 429 && response.StatusCode < 500 {
				response.Body.Close()
				cancel()
				return false, observation, nil
			}
			response.Body.Close()
		}
		cancel()
		if ctx.Err() != nil {
			return false, observation, ctx.Err()
		}
		if attempt < 2 {
			delay := time.Duration(float64(500*time.Millisecond) * float64(int64(1)<<attempt) * (0.5 + rand.Float64()))
			if err := pauseRich(ctx, delay); err != nil {
				return false, observation, err
			}
		}
	}
	return false, observation, &DiscoveryError{Kind: "employer_witness_failed"}
}

func filterNextdataEmployer(ctx context.Context, client *http.Client, jobs []RichMonitorJob, expected string) ([]RichMonitorJob, *GreenhouseResponse, error) {
	unique := map[string]bool{}
	for _, j := range jobs {
		unique[j.URL] = true
	}
	if len(unique) > 500 {
		return nil, nil, &DiscoveryError{Kind: "employer_witness_limit"}
	}
	urls := []string{}
	for url := range unique {
		urls = append(urls, url)
	}
	sort.Strings(urls)
	type witness struct {
		allowed  bool
		response *GreenhouseResponse
		err      error
	}
	results := make([]witness, len(urls))
	operationCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	sem := make(chan struct{}, 8)
	var wg sync.WaitGroup
	for index, url := range urls {
		wg.Add(1)
		go func(index int, url string) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-operationCtx.Done():
				results[index].err = operationCtx.Err()
				return
			}
			allowed, response, err := nextdataEmployerWitness(operationCtx, client, url, expected)
			results[index] = witness{allowed, response, err}
			if err != nil {
				cancel()
			}
		}(index, url)
	}
	wg.Wait()
	for _, r := range results {
		if r.response != nil && r.response.reserved {
			return nil, r.response, r.err
		}
	}
	for _, r := range results {
		if r.err != nil && !errors.Is(r.err, context.Canceled) {
			return nil, r.response, r.err
		}
	}
	for _, r := range results {
		if r.err != nil {
			return nil, r.response, r.err
		}
	}
	allowed := map[string]bool{}
	for i, r := range results {
		if r.allowed {
			allowed[urls[i]] = true
		}
	}
	out := []RichMonitorJob{}
	for _, j := range jobs {
		if allowed[j.URL] {
			out = append(out, j)
		}
	}
	return out, nil, nil
}
