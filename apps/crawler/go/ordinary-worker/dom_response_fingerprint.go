package worker

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"sort"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Mutable document identities are derived from HEAD validators. The existing
// PDF detail reader independently validates the same token against its GET.
func fingerprintDOMURL(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, source string, c dom.ListingConfig) (string, *GreenhouseResponse, error) {
	if client == nil || c.ResponseFingerprintType == "" {
		return "", nil, queue.ErrConfiguration
	}
	initial, err := url.Parse(source)
	if err != nil || initial.Hostname() == "" || initial.User != nil || len(source) > 8192 || initial.Scheme != "https" && initial.Scheme != "http" {
		return "", nil, errPDFBinary
	}
	base, existing, err := pdfFingerprintSource(source)
	if err != nil || existing != "" {
		return "", nil, errPDFBinary
	}
	if len(c.Document.Headers) > 0 {
		board, err := url.Parse(p.Endpoint)
		if err != nil || !pdfSameOrigin(board, initial) {
			return "", nil, errPDFBinary
		}
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if len(c.Document.Headers) > 0 {
		sealed.Jar = nil
	}
	current := base
	var observed *GreenhouseResponse
	for hop := 0; hop <= 5; hop++ {
		target, err := url.Parse(current)
		if err != nil || len(current) > 8192 || target.User != nil || !pdfSameOrigin(initial, target) {
			return "", observed, errPDFBinary
		}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		request, err := http.NewRequestWithContext(requestCtx, "HEAD", current, nil)
		if err != nil {
			cancel()
			return "", observed, errPDFBinary
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		for key, value := range c.Document.Headers {
			request.Header.Set(key, value)
		}
		response, err := sealed.Do(request)
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return "", observed, ctx.Err()
			}
			return "", observed, &DiscoveryError{Kind: "request_failed"}
		}
		response.Body.Close()
		cancel()
		observed = &GreenhouseResponse{endpoint: source, finalURL: current, status: response.StatusCode,
			domVerification: &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, source, domVerificationConfig(config)}}
		reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
		if failure := policy.Check(&runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}, "", current); failure != nil {
			if reserved, ok := failure.(*policy.Reservation); ok {
				observed.reserved, observed.policy, observed.reservationSource = true, reserved.PolicyURL, reserved.Source
			}
			return "", observed, failure
		}
		switch response.StatusCode {
		case 301, 302, 307, 308:
			next, err := response.Location()
			if err != nil || hop == 5 {
				return "", observed, errPDFBinary
			}
			next.Fragment, next.RawFragment = "", ""
			current = next.String()
			continue
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return "", observed, &DiscoveryError{Kind: "inventory_failed", Status: response.StatusCode}
		}
		token, err := responseFingerprintToken(base, c.ResponseFingerprintType, response, nil)
		if err != nil {
			return "", observed, err
		}
		separator := "?"
		if initial.RawQuery != "" {
			separator = "&"
		}
		fingerprinted := base + separator + "_jobseek_fp=" + token
		if len(fingerprinted) > 8192 {
			return "", observed, errPDFBinary
		}
		return fingerprinted, observed, nil
	}
	return "", observed, errPDFBinary
}

func fingerprintDOMInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, in RichDiscovery, c dom.ListingConfig) (RichDiscovery, error) {
	if client == nil || c.ResponseFingerprintType == "" || len(in.Jobs) > 100 {
		in.Jobs = nil
		return in, queue.ErrConfiguration
	}
	jobs := append([]RichMonitorJob{}, in.Jobs...)
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
	urls, failures := make([]string, len(jobs)), make([]error, len(jobs))
	responses := make([]*GreenhouseResponse, len(jobs))
	work, cancel := context.WithCancel(ctx)
	defer cancel()
	tasks := make(chan int)
	var wg sync.WaitGroup
	for n := 0; n < min(4, len(jobs)); n++ {
		wg.Go(func() {
			for i := range tasks {
				if work.Err() != nil {
					failures[i] = work.Err()
					continue
				}
				urls[i], responses[i], failures[i] = fingerprintDOMURL(work, client, p, config, jobs[i].URL, c)
				if failures[i] != nil {
					cancel()
				}
			}
		})
	}
	for i := range jobs {
		select {
		case tasks <- i:
		case <-work.Done():
			failures[i] = work.Err()
		}
	}
	close(tasks)
	wg.Wait()
	in.Jobs = nil
	for _, response := range responses {
		if response != nil && response.reserved {
			in.Response = response
			return in, &DiscoveryError{Kind: "publisher_reserved", Status: response.status}
		}
	}
	for i, failure := range failures {
		if failure != nil && !errors.Is(failure, context.Canceled) {
			if responses[i] != nil {
				in.Response = responses[i]
			}
			return in, failure
		}
	}
	if ctx.Err() != nil {
		return in, ctx.Err()
	}
	for _, failure := range failures {
		if failure != nil {
			return in, failure
		}
	}
	for i := range jobs {
		jobs[i].URL = urls[i]
	}
	in.Jobs = jobs
	return in, nil
}
