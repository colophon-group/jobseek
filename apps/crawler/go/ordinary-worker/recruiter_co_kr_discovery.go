package worker

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand/v2"
	"net/http"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func recruiterKRTransient(status int) bool {
	return status == 429 || status == 500 || status == 502 || status == 503 || status == 504
}
func fetchRecruiterKRPage(ctx context.Context, client *http.Client, b api.RecruiterKROptions, endpoint string, body []byte, wait func(context.Context, time.Duration) error) (*api.Document, *GreenhouseResponse, error) {
	var response *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, observed, err := fetchProviderStatusResource(ctx, client, b, endpoint, body, b.Headers(), 64<<20, map[int]bool{400: true}, true)
		response = observed
		if ctx.Err() != nil {
			return nil, response, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(err, &reserved) {
			return nil, response, err
		}
		status := 0
		if response != nil {
			status = response.status
		}
		if body != nil && status == 400 {
			if d, e := api.Decode(raw); e == nil {
				if obj, ok := d.Value.(map[string]any); ok {
					code, _ := obj["code"].(string)
					if code == "NotFoundCompanyException" || code == "NotFoundPostedDesignException" {
						response.providerDisabled = true
						return nil, response, &DiscoveryError{Kind: "provider_gone", Status: 400}
					}
				}
			}
		}
		retry := recruiterKRTransient(status) || body == nil && status == 0 && err != nil
		if !retry || attempt == 2 {
			if err != nil {
				return nil, response, err
			}
			d, e := api.Decode(raw)
			if e != nil {
				return nil, response, api.ErrInventory
			}
			if _, ok := d.Value.(map[string]any); !ok {
				return nil, response, api.ErrInventory
			}
			return d, response, nil
		}
		jitter := 0.8 + 0.4*rand.Float64()
		if body == nil {
			jitter = 0.5 + rand.Float64()
		}
		if err := wait(ctx, time.Duration(float64(2*time.Second)*float64(uint64(1)<<uint(attempt))*jitter)); err != nil {
			return nil, response, err
		}
	}
	return nil, response, api.ErrInventory
}
func discoverRecruiterKRInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverRecruiterKRInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverRecruiterKRInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, err := api.RecruiterKROptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "recruiter_co_kr" || p.Profile != "recruiter-co-kr.jobflex/v1" || p.Endpoint != b.ListURL() {
		return result, queue.ErrConfiguration
	}
	listClient := *client
	listClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	detailClient := *client
	detailClient.CheckRedirect = client.CheckRedirect
	type summary struct {
		d      *api.Document
		fields map[string]any
		id     string
	}
	summaries := []summary{}
	seen := map[string]bool{}
	for page := 1; page <= 200; page++ {
		payload, e := b.ListPayload(page)
		if e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		d, response, e := fetchRecruiterKRPage(ctx, &listClient, b, b.ListURL(), payload, wait)
		result.Response = response
		if e != nil {
			return RichDiscovery{Response: response}, e
		}
		root := d.Value.(map[string]any)
		rows := []any{}
		if v := root["list"]; v != nil {
			var ok bool
			rows, ok = v.([]any)
			if !ok {
				return RichDiscovery{Response: response}, api.ErrInventory
			}
		}
		for _, row := range rows {
			fields, e := d.RecruiterKRSummary(row, b)
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			if fields == nil {
				result.Truncated = true
				continue
			}
			id, e := d.String(fields["positionSn"])
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			source := fields["url"].(string)
			if seen[source] {
				result.Truncated = true
				continue
			}
			seen[source] = true
			summaries = append(summaries, summary{d, fields, id})
			if len(summaries) >= 20000 {
				result.Truncated = true
				break
			}
		}
		if len(summaries) >= 20000 || len(rows) == 0 {
			break
		}
		pagination, _ := root["pagination"].(map[string]any)
		if total, ok := pagination["totalPages"].(json.Number); ok {
			n, e := total.Int64()
			if e == nil && int64(page) >= n {
				break
			}
		}
		if page == 200 {
			result.Truncated = true
		}
	}
	for start := 0; start < len(summaries); start += 8 {
		end := min(start+8, len(summaries))
		window := summaries[start:end]
		jobs := make([]RichMonitorJob, len(window))
		failures := make([]error, len(window))
		observations := make([]*GreenhouseResponse, len(window))
		var group sync.WaitGroup
		for i, s := range window {
			group.Add(1)
			go func(i int, s summary) {
				defer group.Done()
				d, response, e := fetchRecruiterKRPage(ctx, &detailClient, b, b.DetailURL(s.id), nil, wait)
				observations[i] = response
				var reserved *policy.Reservation
				if errors.As(e, &reserved) {
					failures[i] = e
					return
				}
				if ctx.Err() != nil {
					failures[i] = ctx.Err()
					return
				}
				detail := map[string]any{}
				fieldDoc := s.d
				if e == nil {
					detail = d.Value.(map[string]any)
					fieldDoc = d
				}
				fields, e := fieldDoc.RecruiterKRFields(detail, s.fields)
				if e != nil {
					failures[i] = e
					return
				}
				jobs[i], failures[i] = secondaryRichJob(fields)
			}(i, s)
		}
		group.Wait()
		// A reservation on any completed required resource outranks sibling failure.
		for i, e := range failures {
			var reserved *policy.Reservation
			if errors.As(e, &reserved) {
				return RichDiscovery{Response: observations[i]}, e
			}
		}
		for i, e := range failures {
			if e != nil {
				return RichDiscovery{Response: observations[i]}, e
			}
		}
		for _, job := range jobs {
			if job.URL != "" {
				result.Jobs = append(result.Jobs, job)
			}
		}
	}
	return result, nil
}
