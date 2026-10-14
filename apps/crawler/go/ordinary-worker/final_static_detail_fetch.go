package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func fetchFinalStaticDetail(ctx context.Context, client *http.Client, p q.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	provider := map[string]string{"paycor.public-detail/v1": "paycor", "recruiterbox.public-detail/v1": "recruiterbox", "veryeast.public-detail/v1": "veryeast"}[p.Profile]
	o, e := api.StaticProviderDetailOptionsForSource(provider, p.SourceURL)
	if e != nil || client == nil || wait == nil || p.Endpoint != o.Endpoint {
		return nil, nil, q.ErrConfiguration
	}
	limit := int64(25_000_000)
	attempts := 1
	if provider == "veryeast" {
		limit = 2 << 20
		attempts = 3
	}
	for attempt := 0; attempt < attempts; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		raw, status, _, reserved, failure := staticProviderDetailPage(requestCtx, client, o.Endpoint, limit)
		cancel()
		if reserved != nil {
			return nil, reserved, nil
		}
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		if provider != "veryeast" {
			if failure != nil {
				return nil, nil, failure
			}
			if status != 200 {
				out, e := api.ParseStaticProviderDetail(provider, "")
				return out, nil, e
			}
			out, e := api.ParseStaticProviderDetail(provider, string(raw))
			return out, nil, e
		}
		if failure == nil && status == 200 && strings.TrimSpace(string(raw)) != "" {
			out, e := api.ParseVeryEastDetail(string(raw))
			return out, nil, e
		}
		retry := failure != nil || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status < 600
		var bounded *DiscoveryError
		if errors.As(failure, &bounded) && (bounded.Kind == "body_limit" || bounded.Kind == "redirect") {
			retry = false
		}
		if failure == nil {
			failure = &DiscoveryError{Kind: "http_status", Status: status}
		}
		if !retry || attempt == attempts-1 {
			return nil, nil, failure
		}
		delay := time.Duration(float64(500*time.Millisecond*time.Duration(1<<attempt)) * (.5 + rand.Float64()))
		if e := wait(ctx, delay); e != nil {
			return nil, nil, e
		}
	}
	return nil, nil, q.ErrObservation
}
