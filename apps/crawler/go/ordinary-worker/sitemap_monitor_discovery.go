package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	bounded "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
	sitemap "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

// The parser retains its retry/body budgets; this adapter keeps every request
// in the ordinary worker's sealed transport and origin attribution.
type nativeSitemapSession struct {
	client   *http.Client
	endpoint string
	stats    bounded.Stats
	response *GreenhouseResponse
}

func (s *nativeSitemapSession) Stats() bounded.Stats { return s.stats }

func (s *nativeSitemapSession) Get(ctx context.Context, resource string, headers http.Header) (bounded.Response, error) {
	if resource != s.endpoint || s.stats.Requests >= 3 {
		return bounded.Response{}, &bounded.Error{Kind: bounded.ErrorRequestLimit}
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", resource, nil)
	if err != nil {
		return bounded.Response{}, err
	}
	req.Header = headers.Clone()
	s.stats.Requests++
	s.stats.WireAttempts++
	r, err := s.client.Do(req)
	s.response = nil
	if err != nil {
		return bounded.Response{}, &bounded.Error{Kind: bounded.ErrorTransport, Err: err}
	}
	defer r.Body.Close()
	s.stats.Responses++
	if r.Request == nil || r.Request.URL == nil {
		return bounded.Response{}, queue.ErrObservation
	}
	reservation, policyURL := greenhouseHeaders(r.Header)
	s.response = &GreenhouseResponse{endpoint: resource, finalURL: r.Request.URL.String(), status: r.StatusCode, reserved: reservation == "1", policy: policyURL, reservationSource: "header"}
	response := bounded.Response{StatusCode: r.StatusCode, Header: r.Header.Clone()}
	if s.response.reserved {
		return response, &bounded.Error{Kind: bounded.ErrorTDMReservation}
	}
	remaining := int64(55<<20) - s.stats.DecodedBytes - s.stats.StatusBodyBytes
	if remaining <= 0 {
		return response, &bounded.Error{Kind: bounded.ErrorAggregateLimit}
	}
	limit := min(int64(50<<20), remaining)
	body, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
	if r.StatusCode == 200 {
		s.stats.DecodedBytes += int64(len(body))
	} else {
		s.stats.StatusBodyBytes += int64(len(body))
	}
	var reserved *policy.Reservation
	if errors.As(policy.Check(nil, string(body), s.response.finalURL), &reserved) {
		s.response.reserved = true
		s.response.policy = reserved.PolicyURL
		s.response.reservationSource = reserved.Source
		return response, &bounded.Error{Kind: bounded.ErrorTDMReservation}
	}
	if err != nil {
		return response, &bounded.Error{Kind: bounded.ErrorTransport, Err: err}
	}
	if int64(len(body)) > limit {
		return response, &bounded.Error{Kind: bounded.ErrorBodyLimit}
	}
	response.Body = body
	return response, nil
}

func discoverSitemapInventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	c, include, exclude, err := queue.SitemapMonitorConfig(config)
	if err != nil || client == nil || c.SitemapURL != profile.Endpoint {
		return result, queue.ErrConfiguration
	}
	operationClient := *client
	operationClient.Jar = nil
	operationClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	session := &nativeSitemapSession{client: &operationClient, endpoint: profile.Endpoint}
	found, err := sitemap.RunWithSession(ctx, c, session)
	result.Response = session.response
	result.Truncated = found.Truncated
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if err != nil {
		return result, &DiscoveryError{Kind: "inventory_failed", cause: err}
	}
	inc, err := dom.CompileURLPattern(include)
	if err != nil {
		return result, err
	}
	exc, err := dom.CompileURLPattern(exclude)
	if err != nil {
		return result, err
	}
	for _, raw := range found.URLs {
		if ctx.Err() != nil {
			return RichDiscovery{}, ctx.Err()
		}
		keep, err := inc.MatchString(raw)
		if err != nil {
			return RichDiscovery{}, err
		}
		if !keep {
			continue
		}
		if exclude != "" {
			reject, err := exc.MatchString(raw)
			if err != nil {
				return RichDiscovery{}, err
			}
			if reject {
				continue
			}
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}
