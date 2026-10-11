package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"sort"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type exactListingResource string

func (s exactListingResource) ResourceMatches(raw string) bool { return raw == string(s) }

func fetchDOMTitleCandidate(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, source string, scan *dom.TitleScanConfig, wait func(context.Context, time.Duration) error) (string, *GreenhouseResponse, error) {
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		raw, _, r, err := fetchLastHTTPOnce(ctx, client, exactListingResource(source), api.Request{Method: "GET", URL: source}, 500_000)
		if r != nil {
			observed = r
			r.domVerification = &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, source, domVerificationConfig(config)}
		}
		if r != nil && r.reserved {
			return "", r, err
		}
		if ctx.Err() != nil {
			return "", observed, ctx.Err()
		}
		if r != nil && (r.status == 404 || r.status == 410) {
			return "", observed, nil
		}
		if err == nil && len(raw) > 0 {
			body := jsonld.DecodeDocument(raw, r.contentType)
			classified, e := dom.ClassifyDocument(body, dom.Object{}, source)
			if e != nil || classified["classification"] == "challenge" {
				return "", observed, &DiscoveryError{Kind: "inventory_failed"}
			}
			title, e := scan.DetailTitle(body)
			return title, observed, e
		}
		var de *DiscoveryError
		if errors.As(err, &de) && de.Kind == "body_limit" {
			return "", observed, err
		}
		if r != nil && r.status != 200 && r.status != 202 && r.status != 401 && r.status != 403 && r.status != 408 && r.status != 425 && r.status != 429 && r.status < 500 {
			return "", observed, err
		}
		if attempt == 2 {
			return "", observed, &DiscoveryError{Kind: "inventory_failed", cause: err}
		}
		if e := wait(ctx, time.Duration(float64(500*time.Millisecond)*float64(uint64(1)<<attempt)*(0.5+rand.Float64()))); e != nil {
			return "", observed, e
		}
	}
	return "", observed, &DiscoveryError{Kind: "inventory_failed"}
}

func discoverDOMTitleScan(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, c dom.ListingConfig, source string, in RichDiscovery, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	in.Jobs = nil
	expected, err := c.TitleScan.Expected(source)
	if err != nil {
		return in, err
	}
	urls, err := c.TitleScan.Candidates(p.Endpoint, c.Include)
	if err != nil {
		return in, err
	}
	if client == nil {
		return in, queue.ErrConfiguration
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	work, cancel := context.WithCancel(ctx)
	defer cancel()
	titles := make([]string, len(urls))
	failures := make([]error, len(urls))
	responses := make([]*GreenhouseResponse, len(urls))
	tasks := make(chan int)
	var wg sync.WaitGroup
	for n := 0; n < min(8, len(urls)); n++ {
		wg.Go(func() {
			for i := range tasks {
				if work.Err() != nil {
					failures[i] = work.Err()
					continue
				}
				titles[i], responses[i], failures[i] = fetchDOMTitleCandidate(work, &sealed, p, config, urls[i], c.TitleScan, wait)
				if failures[i] != nil {
					cancel()
				}
			}
		})
	}
	for i := range urls {
		select {
		case tasks <- i:
		case <-work.Done():
			failures[i] = work.Err()
		}
	}
	close(tasks)
	wg.Wait()
	for _, r := range responses {
		if r != nil && r.reserved {
			in.Response = r
			return in, &DiscoveryError{Kind: "publisher_reserved", Status: r.status}
		}
	}
	for _, e := range failures {
		if e != nil && !errors.Is(e, context.Canceled) {
			return in, e
		}
	}
	if ctx.Err() != nil {
		return in, ctx.Err()
	}
	for _, e := range failures {
		if e != nil {
			return in, e
		}
	}
	wanted := map[string]bool{}
	for _, t := range expected {
		wanted[t] = true
	}
	matched := map[string]string{}
	for i, t := range titles {
		if !wanted[t] {
			continue
		}
		if matched[t] != "" {
			return in, dom.ErrTitleScan
		}
		matched[t] = urls[i]
	}
	if len(matched) != len(expected) {
		return in, dom.ErrTitleScan
	}
	out := []string{}
	for _, u := range matched {
		out = append(out, u)
	}
	sort.Strings(out)
	in.Jobs = []RichMonitorJob{}
	for _, u := range out {
		in.Jobs = append(in.Jobs, RichMonitorJob{URL: u, URLOnly: true})
	}
	return in, nil
}
