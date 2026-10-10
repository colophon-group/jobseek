package worker

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand/v2"
	"mime"
	"net/http"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func fetchRemainingHTTPDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	provider := "headhunter"
	if p.Profile == "johdi.api-detail/v1" {
		provider = "johdi"
	}
	config := map[string]any{}
	for key, value := range p.HTTPAPIConfig {
		if key != "board_url" {
			config[key] = value
		}
	}
	raw, e := json.Marshal(config)
	if e != nil {
		return nil, nil, queue.ErrConfiguration
	}
	board, _ := p.HTTPAPIConfig["board_url"].(string)
	o, e := api.RemainingHTTPDetailOptionsFromConfig(provider, board, p.SourceURL, string(raw))
	if e != nil || client == nil || wait == nil || !queue.RemainingHTTPDetailProfile(p.Profile) || o.Endpoint != p.Endpoint || o.Proxy != queue.ProfileRequiresProxy(p.Profile) {
		return nil, nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	request := api.Request{Method: "GET", URL: o.Endpoint, Headers: http.Header{"Accept": {"application/json"}}}
	if provider == "headhunter" {
		request.Headers = api.HeadHunterDetailHeaders(false)
	}
	attempts := 3
	if provider == "headhunter" {
		attempts = 1
	}
	for attempt := 0; attempt < attempts; attempt++ {
		limit := int64(8_000_000)
		if provider == "headhunter" {
			limit = 64 << 20
		}
		body, response, err := fetchRemainingHTTPResource(ctx, &sealed, o, request, limit)
		var reservation *policy.Reservation
		if errors.As(err, &reservation) {
			return nil, reservation, nil
		}
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		status := 0
		if response != nil {
			status = response.status
		}
		if provider == "headhunter" && status == 404 {
			return nil, nil, executor.ErrEmptyResult
		}
		if provider == "headhunter" && status == 403 {
			request = api.Request{Method: "GET", URL: o.SourceURL, Headers: api.HeadHunterDetailHeaders(true)}
			body, response, err = fetchRemainingHTTPResource(ctx, &sealed, o, request, limit)
			if errors.As(err, &reservation) {
				return nil, reservation, nil
			}
			if err != nil {
				return nil, nil, err
			}
			fields, e := jsonld.Parse(o.SourceURL, []byte(jsonld.DecodeDocument(body, response.contentType)), nil)
			if e != nil {
				return nil, nil, e
			}
			title, _ := fields["title"].(string)
			description, _ := fields["description"].(string)
			if strings.TrimSpace(title) == "" || strings.TrimSpace(description) == "" {
				return nil, nil, executor.ErrEmptyResult
			}
			return fields, nil, nil
		}
		if err == nil {
			if provider == "johdi" {
				contentType, _, e := mime.ParseMediaType(response.contentType)
				if e != nil || contentType != "application/json" && !strings.HasSuffix(contentType, "+json") {
					return nil, nil, api.ErrInventory
				}
			}
			d, e := api.Decode(body)
			if e != nil {
				return nil, nil, e
			}
			row, ok := d.Value.(map[string]any)
			if !ok {
				return nil, nil, api.ErrInventory
			}
			if provider == "johdi" {
				fields, e := api.JohdiDetailFields(row, o.ID, o.Locale)
				return fields, nil, e
			}
			fields, e := api.HeadHunterDetailFields(row, o.ID, o.Host)
			return fields, nil, e
		}
		retry := status == 0 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
		if !retry || attempt+1 == attempts {
			return nil, nil, err
		}
		if e = wait(ctx, time.Duration(float64(500*time.Millisecond)*float64(uint64(1)<<uint(attempt))*(0.5+rand.Float64()))); e != nil {
			return nil, nil, e
		}
	}
	return nil, nil, api.ErrInventory
}
