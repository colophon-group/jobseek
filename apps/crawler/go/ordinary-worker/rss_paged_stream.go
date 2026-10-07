package worker

import (
	"context"
	"errors"
	feedsession "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func rssPageURL(feed string, page int, param string) (string, error) {
	return feedsession.PageURL(feed, page, param)
}

type rssPageFetcher func(context.Context, string) (RichDiscovery, error)

// One inventory stream spans all pages. Only original complete200-job batches
// survive a failed direct stream; rendered attempts are collected atomically.
// This traversal alone does not grant ownership to a new configuration.
func collectRSSPages(ctx context.Context, feed string, p *queue.RSSPagination, atomic bool, fetch rssPageFetcher) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	fail := func(err error) (RichDiscovery, error) {
		if atomic {
			return RichDiscovery{Response: result.Response}, err
		}
		prefix, failure := rssValidatedPrefix(result, err)
		prefix.Response = result.Response
		return prefix, failure
	}
	seen := map[string]bool{}
	page := 1
	if p != nil {
		page = p.Start
	}
	for fetched := 1; ; fetched++ {
		if ctx.Err() != nil {
			return fail(ctx.Err())
		}
		endpoint := feed
		if p != nil {
			var err error
			endpoint, err = rssPageURL(feed, page, p.Param)
			if err != nil {
				return fail(err)
			}
		}
		found, err := fetch(ctx, endpoint)
		result.Response = found.Response
		result.Jobs = append(result.Jobs, found.Jobs...)
		result.FeedItems += found.FeedItems
		if len(result.Jobs) >= 50000 {
			result.Jobs = result.Jobs[:50000]
			result.Truncated = true
			return result, nil
		}
		if found.Truncated {
			result.Truncated = true
			return result, nil
		}
		if err != nil {
			return fail(err)
		}
		if p == nil {
			return result, nil
		}
		hasNew := false
		for _, job := range found.Jobs {
			if !seen[job.URL] {
				hasNew = true
			}
			seen[job.URL] = true
		}
		if found.FeedItems >= p.PageSize && !hasNew {
			return fail(errors.New("RSS repeated paginated feed page"))
		}
		if found.FeedItems < p.PageSize {
			return result, nil
		}
		if p.MaxPages != 0 && fetched >= p.MaxPages {
			return fail(errors.New("RSS paginated feed page limit exceeded"))
		}
		if page > 10_000_000-p.Increment {
			return fail(errors.New("RSS page number bound exceeded"))
		}
		page += p.Increment
	}
}
