package worker

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"strconv"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func almaRetry(ctx context.Context, attempt int) error {
	return pauseRich(ctx, time.Duration(float64(500*time.Millisecond)*float64(int64(1)<<attempt)*(0.5+rand.Float64())))
}
func almaReserved(e error) bool { var r *policy.Reservation; return errors.As(e, &r) }
func almaRetryable(response *GreenhouseResponse, e error, graphql bool) bool {
	if response == nil {
		return true
	}
	if graphql && (response.status == 401 || response.status == 403) || response.status == 408 || response.status == 425 || response.status == 429 || response.status >= 500 && response.status < 600 {
		return true
	}
	var failed *DiscoveryError
	return errors.As(e, &failed) && failed.Kind == "body_failed"
}

func fetchAlmaInlineWidget(ctx context.Context, client *http.Client, o api.AlmaOptions) (api.AlmaWidget, *GreenhouseResponse, error) {
	raw, response, e := fetchProviderResource(ctx, client, o, o.RootURL(), nil, nil, 64<<20)
	if ctx.Err() != nil {
		return api.AlmaWidget{}, response, ctx.Err()
	}
	if e != nil {
		if almaReserved(e) {
			return api.AlmaWidget{}, response, e
		}
		return api.AlmaWidget{}, response, nil
	}
	widget, _ := api.ExtractAlmaWidget(string(raw), true)
	return widget, response, nil
}
func fetchAlmaChunkWidget(ctx context.Context, client *http.Client, o api.AlmaOptions) (api.AlmaWidget, *GreenhouseResponse, error) {
	raw, response, e := fetchProviderResource(ctx, client, o, o.RootURL()+"assets/js/react.min.js", nil, nil, 64<<20)
	if ctx.Err() != nil {
		return api.AlmaWidget{}, response, ctx.Err()
	}
	if e != nil {
		if almaReserved(e) {
			return api.AlmaWidget{}, response, e
		}
		return api.AlmaWidget{}, response, nil
	}
	for _, name := range api.AlmaReactChunks(string(raw)) {
		raw, observed, e := fetchProviderResource(ctx, client, o, o.RootURL()+"assets/js/"+name, nil, nil, 64<<20)
		response = observed
		if ctx.Err() != nil {
			return api.AlmaWidget{}, response, ctx.Err()
		}
		if e != nil {
			if almaReserved(e) {
				return api.AlmaWidget{}, response, e
			}
			continue
		}
		if widget, ok := api.ExtractAlmaWidget(string(raw), false); ok {
			return widget, response, nil
		}
	}
	return api.AlmaWidget{}, response, nil
}
func resolveAlmaWidget(ctx context.Context, client *http.Client, o api.AlmaOptions) (api.AlmaWidget, *GreenhouseResponse, error) {
	var last *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, response, e := fetchProviderResource(ctx, client, o, o.ScriptURL(), nil, nil, 64<<20)
		last = response
		if ctx.Err() != nil {
			return api.AlmaWidget{}, last, ctx.Err()
		}
		if almaReserved(e) {
			return api.AlmaWidget{}, last, e
		}
		if response != nil && response.status == 404 {
			widget, observed, e := fetchAlmaInlineWidget(ctx, client, o)
			if e != nil {
				return api.AlmaWidget{}, observed, e
			}
			if widget.ID != "" {
				return widget, observed, nil
			}
			return api.AlmaWidget{}, response, &DiscoveryError{Kind: "provider_gone", Status: 404}
		}
		if e == nil {
			if widget, ok := api.ExtractAlmaWidget(string(raw), false); ok {
				return widget, last, nil
			}
			widget, observed, e := fetchAlmaChunkWidget(ctx, client, o)
			last = observed
			if e != nil {
				return api.AlmaWidget{}, last, e
			}
			if widget.ID != "" {
				return widget, last, nil
			}
			widget, observed, e = fetchAlmaInlineWidget(ctx, client, o)
			last = observed
			if e != nil {
				return api.AlmaWidget{}, last, e
			}
			if widget.ID != "" {
				return widget, last, nil
			}
		} else if !almaRetryable(response, e, false) {
			return api.AlmaWidget{}, last, e
		}
		if attempt < 2 {
			if e := almaRetry(ctx, attempt); e != nil {
				return api.AlmaWidget{}, last, e
			}
		}
	}
	return api.AlmaWidget{}, last, api.ErrInventory
}

func fetchAlmaGraphQL(ctx context.Context, client *http.Client, o api.AlmaOptions, query string, variables map[string]any) (*api.Document, map[string]any, *GreenhouseResponse, error) {
	body, e := json.Marshal(map[string]any{"query": query, "variables": variables})
	if e != nil {
		return nil, nil, nil, e
	}
	var last *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, response, e := fetchProviderResource(ctx, client, o, api.AlmaGraphQLURL, body, api.AlmaHeaders(o.Widget.APIKey, o.Host), 10_000_000)
		last = response
		if ctx.Err() != nil {
			return nil, nil, last, ctx.Err()
		}
		if almaReserved(e) {
			return nil, nil, last, e
		}
		if e == nil && response.status == 200 && len(raw) > 0 {
			d, e := api.Decode(raw)
			if e != nil {
				return nil, nil, last, e
			}
			payload, ok := d.Value.(map[string]any)
			if !ok {
				return nil, nil, last, api.ErrInventory
			}
			data, ok := payload["data"].(map[string]any)
			if !ok {
				if payload["data"] != nil {
					return nil, nil, last, api.ErrInventory
				}
				data = map[string]any{}
			}
			if len(data) == 0 && payload["errors"] != nil {
				errors, ok := payload["errors"].([]any)
				if !ok || len(errors) > 0 {
					return nil, nil, last, api.ErrInventory
				}
			}
			return d, data, last, nil
		}
		if e != nil && !almaRetryable(response, e, true) {
			return nil, nil, last, e
		}
		if response != nil && response.status != 200 && !almaRetryable(response, e, true) {
			return nil, nil, last, api.ErrInventory
		}
		if attempt < 2 {
			if e := almaRetry(ctx, attempt); e != nil {
				return nil, nil, last, e
			}
		}
	}
	return nil, nil, last, api.ErrInventory
}

func almaPageCount(value any) (int, error) {
	if value == nil || value == false || value == "" {
		return 1, nil
	}
	var count int
	var e error
	switch v := value.(type) {
	case json.Number:
		if string(v) == "0" {
			return 1, nil
		}
		n, failure := v.Float64()
		if failure != nil || n > 5000 || n < 0 {
			return 0, api.ErrInventory
		}
		if n == 0 {
			return 1, nil
		}
		count = int(n)
	case string:
		count, e = strconv.Atoi(v)
	case bool:
		count = 1
	default:
		return 0, api.ErrInventory
	}
	if e != nil || count < 1 || count > 5000 {
		return 0, api.ErrInventory
	}
	return count, nil
}

func discoverAlmaInventory(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.AlmaOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || verified == nil || config["monitor_needs_browser"] != "0" || p.Provider != "almacareer" || p.Profile != "almacareer.graphql-items/v1" || p.Endpoint != o.RootURL() {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, e = cookiejar.New(nil)
	if e != nil {
		return result, e
	}
	redirect := verified.CheckRedirect
	client.CheckRedirect = func(next *http.Request, via []*http.Request) error {
		if !o.ResourceMatches(next.URL.String()) {
			return http.ErrUseLastResponse
		}
		if redirect != nil {
			return redirect(next, via)
		}
		if len(via) >= 10 {
			return http.ErrUseLastResponse
		}
		return nil
	}
	if o.Widget.ID == "" || o.Widget.APIKey == "" {
		widget, response, e := resolveAlmaWidget(ctx, &client, o)
		result.Response = response
		if e != nil {
			return result, e
		}
		if o.Widget.ID == "" {
			o.Widget.ID = widget.ID
		}
		if o.Widget.APIKey == "" {
			o.Widget.APIKey = widget.APIKey
		}
		if o.Widget.DetailPath == "" {
			o.Widget.DetailPath = widget.DetailPath
		}
	}
	if o.Widget.DetailPath == "" {
		o.Widget.DetailPath = "detail-pozice"
	}
	jobs := []*api.AlmaJob{}
	seen := map[string]bool{}
	rawCount, lastPage := 0, 1
	for page := 1; page <= lastPage; page++ {
		variables := map[string]any{"widgetId": o.Widget.ID, "host": o.Host, "useExampleData": false, "page": page, "filters": []any{}, "rps": 100}
		d, data, response, e := fetchAlmaGraphQL(ctx, &client, o, api.AlmaListingQuery, variables)
		result.Response = response
		if e != nil {
			return result, e
		}
		widget, ok := data["widget"].(map[string]any)
		if !ok {
			return result, api.ErrInventory
		}
		listing, ok := widget["jobAdList"].(map[string]any)
		if !ok {
			return result, api.ErrInventory
		}
		if len(listing) == 0 {
			break
		}
		rows, e := api.AlmaFlattenGroups(listing["groupedJobAds"])
		if e != nil {
			return result, e
		}
		rawCount += len(rows)
		for _, raw := range rows {
			j, e := api.AlmaProject(d, raw, o.Host, o.Widget.DetailPath, o.Country)
			if e != nil {
				return result, e
			}
			if j != nil && !seen[j.URL] {
				seen[j.URL] = true
				jobs = append(jobs, j)
			}
		}
		paginator, _ := listing["paginator"].(map[string]any)
		lastPage, e = almaPageCount(paginator["lastPage"])
		if e != nil {
			return result, e
		}
		if rawCount >= 50000 {
			result.Truncated = true
			break
		}
	}
	operationCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	var tasks sync.WaitGroup
	var mutex sync.Mutex
	var failure error
	var failedResponse *GreenhouseResponse
	work := make(chan int)
	for range min(8, len(jobs)) {
		tasks.Go(func() {
			for index := range work {
				job := jobs[index]
				id, _ := job.Metadata["id"].(string)
				variables := map[string]any{"widgetId": o.Widget.ID, "host": o.Host, "useExampleData": false, "jobId": id}
				_, data, response, e := fetchAlmaGraphQL(operationCtx, &client, o, api.AlmaDetailQuery, variables)
				if almaReserved(e) {
					mutex.Lock()
					failure = e
					failedResponse = response
					cancel()
					mutex.Unlock()
					continue
				}
				if e != nil {
					continue
				}
				widget, _ := data["widget"].(map[string]any)
				ad, _ := widget["jobAd"].(map[string]any)
				content, _ := ad["content"].(map[string]any)
				if description, ok := content["htmlContent"].(string); ok && strings.TrimSpace(description) != "" {
					job.Description = description
				}
			}
		})
	}
feedJobs:
	for index := range jobs {
		select {
		case work <- index:
		case <-operationCtx.Done():
			break feedJobs
		}
	}
	close(work)
	tasks.Wait()
	if failure != nil {
		return RichDiscovery{Response: failedResponse}, failure
	}
	if ctx.Err() != nil {
		return RichDiscovery{Response: result.Response}, ctx.Err()
	}
	for _, job := range jobs {
		title, e := executor.CoerceText(job.Title)
		if e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		description, e := executor.CoerceText(job.Description)
		if e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		if description != nil {
			description, e = enrichment.NormalizeDescriptionHTML(*description)
			if e != nil {
				return RichDiscovery{Response: result.Response}, e
			}
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: description, Locations: job.Locations, Language: job.Language, DatePosted: job.DatePosted, EmploymentType: job.EmploymentType, Metadata: job.Metadata})
	}
	return result, nil
}
