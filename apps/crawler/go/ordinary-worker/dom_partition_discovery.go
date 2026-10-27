package worker

import (
	"context"
	"errors"
	"mime"
	"net/http"
	"net/http/cookiejar"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type domPartitionPage struct {
	url, source string
	count       int
	listing     RichDiscovery
}

// Each snapshot owns its root, all advertised facets and all leaf pages. A
// count mismatch retries the whole snapshot, never publishes a partial union.
func discoverDOMPartitions(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string, c dom.ListingConfig, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	if client == nil || c.Pagination == nil || c.Pagination.Partitions == nil || wait == nil {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	var response *GreenhouseResponse
	for attempt := 0; attempt < 4; attempt++ {
		scoped := *client
		var err error
		scoped.Jar, err = cookiejar.New(nil)
		if err != nil {
			return RichDiscovery{}, err
		}
		scoped.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
		observation := &lastHTTPObservation{}
		slots := make(chan struct{}, 4)
		fetch := func(call context.Context, raw string, later bool) (domPartitionPage, error) {
			page := domPartitionPage{url: raw, count: -1}
			select {
			case slots <- struct{}{}:
				defer func() { <-slots }()
			case <-call.Done():
				return page, call.Err()
			}
			local := scoped
			if c.Pagination.Partitions.Stateless && raw != profile.Endpoint {
				local.Jar = nil
			}
			opts := c.Document
			opts.SameOrigin = true
			attempts := c.Pagination.Attempts
			if raw == profile.Endpoint {
				attempts = 3
			}
			for n := 0; n < attempts; n++ {
				requestCtx, cancel := context.WithTimeout(call, 30*time.Second)
				doc, failure := jsonld.FetchDocumentWithClient(requestCtx, raw, opts, &local)
				cancel()
				var observed *GreenhouseResponse
				if doc.Responses > 0 {
					var policy *string
					if doc.TDMPolicy != "" {
						v := doc.TDMPolicy
						policy = &v
					}
					observed = &GreenhouseResponse{endpoint: raw, finalURL: doc.FinalURL, status: doc.Status, reserved: doc.ErrorKind == "tdm", policy: policy, reservationSource: doc.TDMSource}
					observation.observe(observed)
					page.listing.Response = observed
				}
				if doc.ErrorKind == "tdm" {
					return page, failure
				}
				if call.Err() != nil {
					return page, call.Err()
				}
				if later && (doc.Status == 404 || doc.Status == 410 || (doc.Status == 401 || doc.Status == 403) && !c.Pagination.Transient403) {
					return page, nil
				}
				if failure == nil && doc.Status == 200 && len(doc.Body) > 0 {
					contentType := doc.ContentType
					if c.Encoding != "" {
						contentType = mime.FormatMediaType("text/html", map[string]string{"charset": c.Encoding})
					}
					page.source = jsonld.DecodeDocument(doc.Body, contentType)
					// Original HTTP pagination previews at most 500,000 codepoints.
					count := 0
					for offset := range page.source {
						if count == 500000 {
							page.source = page.source[:offset]
							break
						}
						count++
					}
					p := profile
					p.Endpoint = raw
					var parseErr error
					page.listing, parseErr = parseDOMInventory(call, page.listing, p, c, page.source, "", false, true)
					if parseErr != nil {
						return page, parseErr
					}
					return page, nil
				}
				if failure == nil && doc.Status != 200 && doc.Status != 202 && doc.Status != 401 && doc.Status != 403 && doc.Status != 408 && doc.Status != 425 && doc.Status != 429 && doc.Status < 500 {
					return page, dom.ErrListingPartition
				}
				if n+1 < attempts {
					if e := wait(call, time.Duration(1<<n)*500*time.Millisecond); e != nil {
						return page, e
					}
				}
			}
			return page, dom.ErrListingPartition
		}
		found, err := collectDOMPartitions(ctx, profile, config, c, fetch)
		response = observation.latest()
		found.Response = response
		if err == nil {
			return found, nil
		}
		if !errors.Is(err, dom.ErrPartitionSnapshot) || attempt == 3 {
			return RichDiscovery{Response: response}, err
		}
		if err = wait(ctx, time.Duration(1<<attempt)*time.Second); err != nil {
			return RichDiscovery{Response: response}, err
		}
	}
	return RichDiscovery{Response: response}, dom.ErrPartitionSnapshot
}

// The transport callback is shared by live verified HTTP and frozen original
// response tests. It returns no selected rows; the ordinary writer remains the
// only publication boundary.
func collectDOMPartitions(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string, c dom.ListingConfig, fetch func(context.Context, string, bool) (domPartitionPage, error)) (RichDiscovery, error) {
	p := c.Pagination.Partitions
	var requested atomic.Int32
	rules, err := queue.FeedMonitorURLRules(config)
	if err != nil {
		return RichDiscovery{}, err
	}
	identityUnion := func(groups ...[]RichMonitorJob) []RichMonitorJob {
		seen := map[string]bool{}
		out := []RichMonitorJob{}
		ordered := []RichMonitorJob{}
		for _, jobs := range groups {
			ordered = append(ordered, jobs...)
		}
		sort.Slice(ordered, func(i, j int) bool { return ordered[i].URL < ordered[j].URL })
		for _, job := range ordered {
			key := rules.Rewrite(job.URL)
			if !seen[key] {
				seen[key] = true
				out = append(out, job)
			}
		}
		sort.Slice(out, func(i, j int) bool { return out[i].URL < out[j].URL })
		return out
	}
	parallel := func(call context.Context, urls []string, fn func(context.Context, string) ([]RichMonitorJob, error)) ([][]RichMonitorJob, error) {
		work, cancel := context.WithCancel(call)
		defer cancel()
		out := make([][]RichMonitorJob, len(urls))
		failures := make([]error, len(urls))
		var wg sync.WaitGroup
		for i, raw := range urls {
			wg.Go(func() {
				out[i], failures[i] = fn(work, raw)
				if failures[i] != nil {
					cancel()
				}
			})
		}
		wg.Wait()
		for _, e := range failures {
			if e != nil && !errors.Is(e, context.Canceled) {
				return nil, e
			}
		}
		for _, e := range failures {
			if e != nil {
				return nil, e
			}
		}
		return out, nil
	}
	readPartition := func(call context.Context, raw string) (domPartitionPage, error) {
		if requested.Add(1) > 500 {
			return domPartitionPage{}, dom.ErrListingPartition
		}
		page, e := fetch(call, raw, false)
		if e != nil {
			return page, e
		}
		page.count, e = p.Count(page.source)
		if e != nil {
			return page, e
		}
		if len(page.listing.Jobs) == 0 {
			if page.count >= 0 {
				return page, dom.ErrPartitionSnapshot
			}
			return page, dom.ErrListingPartition
		}
		return page, nil
	}
	leaf := func(call context.Context, page domPartitionPage) ([]RichMonitorJob, error) {
		pc := profile
		pc.Endpoint = page.url
		lc := c
		lp := *c.Pagination
		lp.Partitions = nil
		lc.Pagination = &lp
		found, e := collectDOMListingPages(call, pc, config, lc, func(next queue.GreenhouseMonitorProfile, _ dom.ListingConfig, later bool) (RichDiscovery, error) {
			if !later {
				return page.listing, nil
			}
			v, e := fetch(call, next.Endpoint, true)
			return v.listing, e
		})
		if e != nil {
			return nil, e
		}
		if found.Truncated {
			return nil, dom.ErrListingPartition
		}
		if page.count >= 0 && len(found.Jobs) != page.count {
			return nil, dom.ErrPartitionSnapshot
		}
		return found.Jobs, nil
	}
	children := func(page domPartitionPage, selector string) ([]string, error) {
		return p.Links(page.source, page.url, selector)
	}
	var path func(context.Context, domPartitionPage, []string, bool) ([]RichMonitorJob, error)
	path = func(call context.Context, page domPartitionPage, selectors []string, exact bool) ([]RichMonitorJob, error) {
		if p.Limit == 0 || page.count < 0 || page.count <= p.Limit {
			return leaf(call, page)
		}
		if len(selectors) == 0 {
			return nil, dom.ErrListingPartition
		}
		urls, e := children(page, selectors[0])
		if e != nil {
			return nil, e
		}
		var total atomic.Int64
		groups, e := parallel(call, urls, func(childCtx context.Context, raw string) ([]RichMonitorJob, error) {
			child, e := readPartition(childCtx, raw)
			if e != nil {
				return nil, e
			}
			total.Add(int64(child.count))
			return path(childCtx, child, selectors[1:], exact)
		})
		if e != nil {
			return nil, e
		}
		if exact && total.Load() != int64(page.count) {
			return nil, dom.ErrPartitionSnapshot
		}
		return identityUnion(groups...), nil
	}
	root, e := fetch(ctx, profile.Endpoint, false)
	if e != nil {
		return RichDiscovery{}, e
	}
	expected := -1
	if p.ValidateTotal {
		expected, e = p.Count(root.source)
		if e != nil {
			return RichDiscovery{}, e
		}
	}
	urls, e := children(root, p.Selector)
	if e != nil {
		return RichDiscovery{}, e
	}
	var total atomic.Int64
	groups, e := parallel(ctx, urls, func(call context.Context, raw string) ([]RichMonitorJob, error) {
		page, e := readPartition(call, raw)
		if e != nil {
			return nil, e
		}
		total.Add(int64(page.count))
		if len(p.CoverPaths) > 0 && page.count > p.Limit {
			paths := [][]RichMonitorJob{}
			for _, selectors := range p.CoverPaths {
				jobs, e := path(call, page, selectors, false)
				if e != nil {
					return nil, e
				}
				paths = append(paths, jobs)
			}
			jobs := identityUnion(paths...)
			if len(jobs) != page.count {
				return nil, dom.ErrPartitionSnapshot
			}
			return jobs, nil
		}
		return path(call, page, p.Fallback, true)
	})
	if e != nil {
		return RichDiscovery{}, e
	}
	if expected >= 0 && total.Load() != int64(expected) {
		return RichDiscovery{}, dom.ErrPartitionSnapshot
	}
	jobs := identityUnion(groups...)
	if expected >= 0 && len(jobs) != expected {
		return RichDiscovery{}, dom.ErrPartitionSnapshot
	}
	if len(jobs) > 50000 {
		return RichDiscovery{}, dom.ErrListingPartition
	}
	return RichDiscovery{Jobs: jobs}, nil
}
