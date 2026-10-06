package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"time"
)

type fourthExactResource string

func (s fourthExactResource) ResourceMatches(v string) bool { return string(s) == v }
func fetchRipplingDetail(ctx context.Context, client *http.Client, source, override string) (map[string]any, *policy.Reservation, error) {
	o, id, e := api.RipplingDetailRoute(source, override)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	endpoint := o.DetailURL(id)
	raw, response, e := fetchProviderResource(ctx, client, fourthExactResource(endpoint), endpoint, nil, nil, 64<<20)
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		if response != nil {
			return nil, nil, executor.ErrEmptyResult
		}
		return nil, nil, e
	}
	d, e := api.Decode(raw)
	if e != nil {
		return nil, nil, e
	}
	fields, e := api.RipplingDetailFields(d)
	if e != nil {
		return nil, nil, e
	}
	if locs, ok := fields["locations"].([]string); ok {
		done := false
		for _, loc := range locs {
			for _, token := range []string{"remote", "hybrid", "onsite", "on-site", "on site"} {
				if strings.Contains(strings.ToLower(loc), token) {
					if v := enrichment.NormalizeJobLocationType(token); v != "" {
						fields["job_location_type"] = v
						done = true
						break
					}
				}
			}
			if done {
				break
			}
		}
	}
	return fields, nil, nil
}
func fetchPaycomDetail(ctx context.Context, client *http.Client, profile queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	return fetchPaycomDetailWithWait(ctx, client, profile, pauseRich)
}
func fetchPaycomDetailWithWait(ctx context.Context, client *http.Client, profile queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	o, id, e := api.PaycomDetailRoute(profile.SourceURL)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	defaults, e := api.PaycomDefaultLocations(profile.HTTPAPIConfig)
	if e != nil {
		return nil, nil, e
	}
	op := *client
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	b, _, e := fetchPaycomBootstrap(ctx, &op, o, wait)
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		return nil, nil, e
	}
	endpoint := b.DetailURL(id)
	d, response, e := fetchSecondaryJSONPage(ctx, &op, fourthExactResource(endpoint), endpoint, nil, b.Headers, nil, wait)
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		if response != nil && (response.status == 404 || response.status == 410) {
			return nil, nil, executor.ErrEmptyResult
		}
		return nil, nil, e
	}
	fields, salary, e := api.PaycomDetailFields(d, defaults)
	if e != nil {
		return nil, nil, e
	}
	if kind, ok := fields["job_location_type"].(string); ok {
		v := enrichment.NormalizeJobLocationType(kind)
		fields["job_location_type"] = nil
		if v != "" {
			fields["job_location_type"] = v
		}
	}
	if salary != "" {
		parsed, e := enrichment.Salary(salary, nil)
		if e != nil {
			return nil, nil, e
		}
		if parsed.Parsed != nil {
			fields["base_salary"] = parsed.Parsed
		}
	}
	return fields, nil, nil
}
