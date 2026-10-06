package worker

import (
	"context"
	"net/http"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverUKGInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverUKGInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverUKGInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, err := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || config["monitor_needs_browser"] != "0" || p.Provider != "ukg" || p.Profile != "ukg.search-items/v1" || p.Endpoint != b.SearchURL() {
		return result, queue.ErrConfiguration
	}
	op := *client
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	seen := map[string]bool{}
	rawSeen := int64(0)
	expected := int64(-1)
	changed := false
	invalid, duplicates := 0, 0
	fetch := func() (*api.Document, int64, []any, error) {
		payload, err := api.UKGSearchPayload(int(rawSeen), 100)
		if err != nil {
			return nil, 0, nil, err
		}
		d, response, err := fetchSecondaryJSONPage(ctx, &op, b, b.SearchURL(), payload, nil, map[int]bool{202: true, 401: true, 403: true}, wait)
		result.Response = response
		if err != nil {
			return nil, 0, nil, err
		}
		total, rows, err := api.UKGPage(d)
		return d, total, rows, err
	}
	for page := 0; page < 500; page++ {
		_, total, rows, err := fetch()
		if err != nil {
			if page == 0 && result.Response != nil && (result.Response.status == 404 || result.Response.status == 410) {
				err = &DiscoveryError{Kind: "provider_gone", Status: result.Response.status}
			}
			return RichDiscovery{Response: result.Response}, err
		}
		if expected < 0 {
			expected = total
		} else if expected != total {
			expected = total
			changed = true
		}
		if len(rows) > 100 || rawSeen+int64(len(rows)) > total {
			return RichDiscovery{Response: result.Response}, api.ErrInventory
		}
		if len(rows) == 0 && rawSeen < total {
			_, retryTotal, retryRows, err := fetch()
			if err != nil {
				return RichDiscovery{Response: result.Response}, err
			}
			if retryTotal != expected {
				expected = retryTotal
				changed = true
			}
			total, rows = retryTotal, retryRows
			if len(rows) == 0 && rawSeen < total {
				return RichDiscovery{Response: result.Response}, api.ErrInventory
			}
		}
		if len(rows) > 0 && rawSeen+int64(len(rows)) < total && len(rows) < 100 {
			_, retryTotal, retryRows, err := fetch()
			if err != nil {
				return RichDiscovery{Response: result.Response}, err
			}
			if retryTotal != expected {
				expected = retryTotal
				changed = true
			}
			total = retryTotal
			if len(retryRows) > 100 || rawSeen+int64(len(retryRows)) > total || rawSeen+int64(len(retryRows)) < total && len(retryRows) < 100 {
				return RichDiscovery{Response: result.Response}, api.ErrInventory
			}
			rows = retryRows
		}
		for _, row := range rows {
			rawSeen++
			fields := api.UKGFields(row, b)
			if fields == nil {
				invalid++
				continue
			}
			source := fields["url"].(string)
			if seen[source] {
				duplicates++
				continue
			}
			seen[source] = true
			job, err := secondaryRichJob(fields)
			if err != nil {
				return RichDiscovery{Response: result.Response}, err
			}
			result.Jobs = append(result.Jobs, job)
		}
		if rawSeen >= total || rawSeen >= 50000 {
			if expected > 0 && len(seen) == 0 {
				return RichDiscovery{Response: result.Response}, api.ErrInventory
			}
			result.Truncated = changed || invalid > 0 || duplicates > 0 || rawSeen != total || total > 50000
			return result, nil
		}
	}
	result.Truncated = true
	return result, nil
}
