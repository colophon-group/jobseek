package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Session detail requests use a fresh per-operation cookie jar on the same
// verified transport as discovery. The compiled profile and tenant stay bound.
func fetchLastHTTPDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	provider := map[string]string{"infor.session-detail/v1": "infor", "peoplesoft.session-detail/v1": "peoplesoft"}[p.Profile]
	board, _ := p.HTTPAPIConfig["board_url"].(string)
	metadata := map[string]any{}
	for key, value := range p.HTTPAPIConfig {
		if key != "board_url" {
			metadata[key] = value
		}
	}
	raw, err := json.Marshal(metadata)
	if err != nil {
		return nil, nil, queue.ErrConfiguration
	}
	o, err := api.LastHTTPDetailOptionsFromConfig(provider, board, p.SourceURL, string(raw))
	if err != nil || p.Profile != o.Profile() || p.Endpoint != o.Endpoint {
		return nil, nil, queue.ErrConfiguration
	}
	observation := &lastHTTPObservation{}
	fetch, err := lastHTTPFetcher(client, o.LastHTTPOptions, o, wait, observation)
	if err != nil {
		return nil, nil, err
	}
	var fields map[string]any
	if provider == "infor" {
		fields, err = api.FetchInforDetail(ctx, o, fetch)
	} else {
		fields, err = api.FetchPeopleSoftDetail(ctx, o, func(ctx context.Context, r api.Request) ([]byte, error) { b, _, e := fetch(ctx, r); return b, e },
			func(s string) any {
				if value := executor.EmploymentType(&s); value != nil {
					return *value
				}
				return nil
			},
			func(s string) any {
				if value := enrichment.NormalizeJobLocationType(s); value != "" {
					return value
				}
				return nil
			},
			func(s string) (any, error) {
				result, e := enrichment.Salary(s, nil)
				if e != nil {
					return nil, e
				}
				if result != nil && result.Parsed != nil {
					return result.Parsed, nil
				}
				return nil, nil
			})
	}
	var reserved *policy.Reservation
	if errors.As(err, &reserved) {
		return nil, reserved, nil
	}
	if err != nil {
		return nil, nil, err
	}
	return fields, nil, nil
}
