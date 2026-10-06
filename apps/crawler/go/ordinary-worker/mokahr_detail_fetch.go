package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func mokahrDetailValues(raw map[string]any, cities map[int]string) (map[string]any, error) {
	fields, e := api.MokahrFields(raw, cities)
	if e != nil {
		return nil, e
	}
	employment, e := executor.CoerceText(fields.EmploymentType)
	if e != nil {
		return nil, e
	}
	employment = executor.ScraperEmploymentType(employment)
	date := raw["publishedAt"]
	if date == nil || date == "" || date == false {
		date = raw["openedAt"]
	}
	if date == "" || date == false {
		date = nil
	}
	if value, ok := date.(string); ok {
		if first, _, found := strings.Cut(value, "T"); found {
			date = first
		}
	}
	var metadata map[string]any
	if len(fields.Metadata) > 0 {
		metadata = fields.Metadata
	}
	title, description := fields.Title, fields.Description
	if title == "" || title == false {
		title = nil
	}
	if description == "" || description == false {
		description = nil
	}
	return map[string]any{"title": title, "description": description, "locations": fields.Locations, "employment_type": employment, "job_location_type": nil, "date_posted": date, "base_salary": fields.BaseSalary, "language": nil, "extras": fields.Extras, "metadata": metadata}, nil
}

// The legacy opposite-route bootstrap supports older campus URLs. It stays on
// the same configured origin/org/site; publisher policy is never a soft miss.
func fetchMokahrDetail(ctx context.Context, client *http.Client, source, locale string) (map[string]any, *policy.Reservation, error) {
	route, e := api.MokahrDetailRouteForSource(source)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	if locale == "" {
		locale = "zh-CN"
	}
	var iv string
	var cities map[int]string
	for _, partition := range []api.MokahrPartition{route.Partition, route.FallbackPartition()} {
		raw, response, e := fetchProviderResource(ctx, client, route, partition.PageURL, nil, nil, 64<<20)
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			return nil, reserved, nil
		}
		if e != nil {
			if response == nil {
				return nil, nil, e
			}
			continue
		}
		iv, cities, e = api.MokahrDetailBootstrap(string(raw), partition)
		if e == nil && iv != "" {
			break
		}
		iv = ""
	}
	if iv == "" {
		return nil, nil, executor.ErrEmptyResult
	}
	body, _ := json.Marshal(map[string]any{"orgId": route.Partition.OrgID, "siteId": route.Partition.SiteID, "jobId": route.JobID, "locale": locale})
	// The bootstrap follows redirects; the encrypted POST retains the Python
	// client's default of refusing redirects and never replays its body.
	postClient := *client
	postClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	raw, response, e := fetchProviderResource(ctx, &postClient, route, route.APIURL(), body, nil, 64<<20)
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil || response == nil || response.status != 200 {
		return nil, nil, executor.ErrEmptyResult
	}
	d, e := api.Decode(raw)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	d, e = api.MokahrDecrypt(d, iv)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	outer, ok := d.Value.(map[string]any)
	if !ok {
		return nil, nil, executor.ErrEmptyResult
	}
	detail, ok := outer["data"].(map[string]any)
	if !ok {
		return nil, nil, executor.ErrEmptyResult
	}
	returnedID, hasID := detail["id"].(string)
	if hasID && returnedID != route.JobID {
		return nil, nil, executor.ErrEmptyResult
	}
	values, e := mokahrDetailValues(detail, cities)
	if e != nil {
		return nil, nil, e
	}
	return values, nil, nil
}
