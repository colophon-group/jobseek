package worker

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"golang.org/x/sync/errgroup"
)

var sfJobInviteRedirect = regexp.MustCompile(`^/job/[^/?#]+/([1-9][0-9]{0,11})-([a-z]{2}_[A-Z]{2})/$`)

const rssIdentityRequestInterval = 200 * time.Millisecond

// Limit the opt-in identity lookup's request starts, including retries. A
// concurrency bound alone still bursts into the publisher's rate limit.
type rssIdentityPacedTransport struct {
	inner    http.RoundTripper
	mu       sync.Mutex
	next     time.Time
	interval time.Duration
}

func (p *rssIdentityPacedTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if err := request.Context().Err(); err != nil {
		return nil, err
	}
	p.mu.Lock()
	delay := max(time.Until(p.next), 0)
	p.next = time.Now().Add(delay + p.interval)
	p.mu.Unlock()
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	case <-timer.C:
		if err := request.Context().Err(); err != nil {
			return nil, err
		}
		return p.inner.RoundTrip(request)
	}
}

// Resolve aliases before the canonical URL transform and preferred-locale
// selection. No failed or partially resolved inventory can retire postings.
func resolveRSSJobInviteIdentities(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, inventory RichDiscovery) (RichDiscovery, error) {
	if verified == nil || !profile.RSSResolveJobInviteIdentity || len(inventory.Jobs) > 2000 || inventory.Truncated {
		return RichDiscovery{Response: inventory.Response}, queue.ErrConfiguration
	}
	client := *verified
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	inner := client.Transport
	if inner == nil {
		inner = http.DefaultTransport
	}
	client.Transport = &rssIdentityPacedTransport{inner: inner, interval: rssIdentityRequestInterval}
	jobs := append([]RichMonitorJob(nil), inventory.Jobs...)
	responses := make([]*GreenhouseResponse, len(jobs))
	group, call := errgroup.WithContext(ctx)
	group.SetLimit(16)
	for i := range jobs {
		group.Go(func() error {
			resolved, observed, err := resolveRSSJobInviteIdentity(call, &client, profile, jobs[i])
			responses[i] = observed
			if err == nil {
				jobs[i] = resolved
			}
			return err
		})
	}
	err := group.Wait()
	for _, response := range responses {
		if response != nil && response.reserved {
			return RichDiscovery{Response: response}, &DiscoveryError{Kind: "publisher_reserved", Status: response.status}
		}
	}
	if err != nil {
		return RichDiscovery{Response: inventory.Response}, &DiscoveryError{Kind: "feed_failed", cause: err}
	}
	inventory.Jobs = jobs
	return inventory, nil
}

func resolveRSSJobInviteIdentity(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, job RichMonitorJob) (RichMonitorJob, *GreenhouseResponse, error) {
	if !queue.RSSDetailMonitorResourceMatches(profile, job.URL) {
		return job, nil, queue.ErrConfiguration
	}
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		call, cancel := context.WithTimeout(ctx, 30*time.Second)
		request, err := http.NewRequestWithContext(call, http.MethodHead, job.URL, nil)
		if err != nil {
			cancel()
			return job, observed, queue.ErrConfiguration
		}
		request.Header.Set("User-Agent", ordinaryUserAgent)
		request.Header.Set("Accept", ordinaryAccept)
		response, err := client.Do(request)
		if err == nil {
			response.Body.Close()
			if response.Request == nil || response.Request.URL == nil || response.Request.URL.String() != job.URL {
				cancel()
				return job, observed, queue.ErrConfiguration
			}
			observed = &GreenhouseResponse{endpoint: job.URL, finalURL: job.URL, status: response.StatusCode, location: response.Header.Get("Location")}
			reservation, policyURL := greenhouseHeaders(response.Header)
			signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: policyURL}
			if err = policy.Check(signals, "", job.URL); err != nil {
				var reserved *policy.Reservation
				if errors.As(err, &reserved) {
					observed.reserved = true
					observed.policy = reserved.PolicyURL
					observed.reservationSource = reserved.Source
				}
				cancel()
				return job, observed, err
			}
			status := response.StatusCode
			if status == 301 || status == 302 || status == 303 || status == 307 || status == 308 {
				next, err := pythonJoinURL(job.URL, observed.location)
				u, parseErr := url.Parse(next)
				// Validate the escaped resource form while retaining urllib’s original
				// Unicode path for the configured canonical transform and locale ranking.
				if err != nil || parseErr != nil || observed.location == "" || !queue.RSSDetailMonitorResourceMatches(profile, u.String()) || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
					cancel()
					return job, observed, queue.ErrConfiguration
				}
				match := sfJobInviteRedirect.FindStringSubmatch(u.EscapedPath())
				if match == nil {
					cancel()
					return job, observed, queue.ErrConfiguration
				}
				metadata := make(map[string]any, len(job.Metadata)+3)
				for key, value := range job.Metadata {
					metadata[key] = value
				}
				if id, ok := metadata["id"]; ok && id != nil {
					metadata["feed_id"] = id
				}
				metadata["job_invite_id"], metadata["job_locale"] = match[1], match[2]
				job.URL, job.Metadata = next, metadata
				cancel()
				return job, observed, nil
			}
			if status != 401 && status != 403 && status != 408 && status != 425 && status != 429 && status < 500 {
				cancel()
				return job, observed, errors.New("SuccessFactors identity expected redirect")
			}
		}
		cancel()
		if ctx.Err() != nil {
			return job, observed, ctx.Err()
		}
		if attempt == 2 {
			return job, observed, errors.New("SuccessFactors identity exhausted retries")
		}
		if err := almaRetry(ctx, attempt); err != nil {
			return job, observed, err
		}
	}
	return job, observed, errors.New("SuccessFactors identity missing response")
}
