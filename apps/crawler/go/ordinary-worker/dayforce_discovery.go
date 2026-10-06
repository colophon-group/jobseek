package worker

import (
	"context"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Dayforce pagination consumes pages from one source-bound browser session.
// This function grants no network, profile-admission or persistence authority.
// A failed page cannot expose a complete prefix to ordinary gone detection.
func discoverDayforcePages(ctx context.Context, board api.DayforceBoard, site api.DayforceSite, overlap int, fetch func(context.Context, int) (*api.Document, *GreenhouseResponse, error)) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	if overlap < 0 || overlap >= api.DayforcePageSize || fetch == nil || site.Disabled {
		return out, api.ErrOptions
	}
	step := api.DayforcePageSize - overlap
	maxPages := (50000 + step - 1) / step
	expected := int64(-1)
	countChanged, invalid, withinPageDuplicate := false, false, false
	seen := map[string]bool{}
	for pageNumber, offset := 0, 0; pageNumber < maxPages; pageNumber, offset = pageNumber+1, offset+step {
		if e := ctx.Err(); e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		payload, response, e := fetch(ctx, offset)
		out.Response = response
		if e != nil {
			return RichDiscovery{Response: response}, e
		}
		total, rows, e := api.DayforcePage(payload, board, site, offset)
		if e != nil {
			return RichDiscovery{Response: response}, e
		}
		if expected >= 0 && total != expected {
			countChanged = true
		}
		expected = total
		if len(rows) == 0 && int64(offset) < total {
			payload, response, e = fetch(ctx, offset)
			out.Response = response
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			retryTotal, retryRows, e := api.DayforcePage(payload, board, site, offset)
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			if retryTotal != expected {
				countChanged = true
			}
			expected, rows = retryTotal, retryRows
			// The established monitor retains the initial page's total for
			// coverage checks after this one explicit premature-empty retry.
			if len(rows) == 0 {
				return RichDiscovery{Response: response}, &DiscoveryError{Kind: "premature_empty", Status: 200}
			}
		}
		pageURLs := map[string]bool{}
		for _, raw := range rows {
			fields := api.DayforceJobFields(raw, board, site)
			if fields == nil {
				invalid = true
				continue
			}
			source := fields["url"].(string)
			if pageURLs[source] {
				withinPageDuplicate = true
				continue
			}
			pageURLs[source] = true
			if seen[source] {
				continue
			}
			seen[source] = true
			job, e := secondaryRichJob(fields)
			if e != nil {
				return RichDiscovery{Response: response}, e
			}
			out.Jobs = append(out.Jobs, job)
		}
		coverageEnd := int64(offset + len(rows))
		if coverageEnd < total && len(rows) < api.DayforcePageSize {
			return RichDiscovery{Response: response}, &DiscoveryError{Kind: "premature_partial", Status: 200}
		}
		if coverageEnd >= total || coverageEnd >= 50000 {
			if expected > 0 && len(seen) == 0 {
				return RichDiscovery{Response: response}, api.ErrInventory
			}
			out.Truncated = countChanged || invalid || withinPageDuplicate || total <= 50000 && int64(len(seen)) != total || total > 50000
			return out, nil
		}
	}
	out.Truncated = true
	return out, nil
}
