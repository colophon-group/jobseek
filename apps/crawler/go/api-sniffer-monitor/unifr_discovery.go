package apisniffer

import (
	"context"
	"errors"
	"sort"
	"sync"
	"time"
)

type UnifrFetch func(context.Context, string, string) ([]byte, error)

func unifrListings(ctx context.Context, fetch UnifrFetch) (map[string]map[string]string, error) {
	out := map[string]map[string]string{}
	for _, locale := range []string{"fr", "de"} {
		resource := UnifrCentralFR
		if locale == "de" {
			resource = UnifrCentralDE
		}
		body, err := fetch(ctx, resource, "html")
		if err != nil {
			return nil, err
		}
		rows, err := UnifrCentralListing(body, locale)
		if err != nil {
			return nil, err
		}
		out[locale] = rows
	}
	return out, nil
}

func DiscoverUnifr(ctx context.Context, o UnifrOptions, today time.Time, fetch UnifrFetch) ([]UnifrJob, error) {
	if ctx == nil || fetch == nil {
		return nil, ErrOptions
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	switch o.Kind {
	case "central":
		listings, err := unifrListings(ctx, fetch)
		if err != nil {
			return nil, err
		}
		return unifrFetchCentralDetails(ctx, listings, today, fetch)
	case "links":
		body, err := fetch(ctx, o.URL, "html")
		if err != nil {
			return nil, err
		}
		urls, err := UnifrLinkInventory(body, o)
		if err != nil {
			return nil, err
		}
		out := make([]UnifrJob, 0, len(urls))
		for _, resource := range urls {
			out = append(out, UnifrJob{URL: resource, URLOnly: true})
		}
		return out, ctx.Err()
	case "accordion":
		body, err := fetch(ctx, o.URL, "html")
		if err != nil {
			return nil, err
		}
		items, err := UnifrAccordionItems(body, o)
		if err != nil {
			return nil, err
		}
		central := map[string]bool{}
		if len(o.ExcludedCentralIDs) > 0 {
			listings, err := unifrListings(ctx, fetch)
			if err != nil {
				return nil, err
			}
			for _, rows := range listings {
				for id := range rows {
					central[id] = true
				}
			}
		}
		jobs, err := UnifrAccordionJobs(items, o, central, today)
		if err != nil {
			return nil, err
		}
		return jobs, ctx.Err()
	default:
		return nil, ErrOptions
	}
}

func unifrFetchCentralDetails(ctx context.Context, listings map[string]map[string]string, today time.Time, fetch UnifrFetch) ([]UnifrJob, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	type request struct{ id, locale, title string }
	requests := []request{}
	ids := map[string]bool{}
	for _, locale := range []string{"fr", "de"} {
		for id, title := range listings[locale] {
			requests = append(requests, request{id, locale, title})
			ids[id] = true
		}
	}
	if len(ids) == 0 || len(ids) > 200 || len(requests) > 200 {
		return nil, ErrInventory
	}
	sort.Slice(requests, func(i, j int) bool {
		if requests[i].id != requests[j].id {
			return requests[i].id < requests[j].id
		}
		return requests[i].locale < requests[j].locale
	})
	type observation struct {
		request request
		fields  map[string]string
		err     error
	}
	results := make(chan observation, len(requests))
	slots := make(chan struct{}, 8)
	var wait sync.WaitGroup
	for _, r := range requests {
		wait.Add(1)
		go func(r request) {
			defer wait.Done()
			select {
			case slots <- struct{}{}:
				defer func() { <-slots }()
			case <-ctx.Done():
				results <- observation{request: r, err: ctx.Err()}
				return
			}
			body, err := fetch(ctx, UnifrDetailRoot+"/"+r.locale+"/"+r.id, "json")
			var fields map[string]string
			if err == nil {
				fields, err = UnifrCentralDetail(body, r.id, r.locale, r.title, today)
			}
			results <- observation{request: r, fields: fields, err: err}
			if err != nil {
				cancel()
			}
		}(r)
	}
	wait.Wait()
	close(results)
	details := map[string]map[string]map[string]string{}
	var failure error
	for observed := range results {
		if observed.err != nil {
			if failure == nil || errors.Is(failure, context.Canceled) {
				failure = observed.err
			}
			continue
		}
		if details[observed.request.id] == nil {
			details[observed.request.id] = map[string]map[string]string{}
		}
		details[observed.request.id][observed.request.locale] = observed.fields
	}
	if failure != nil {
		return nil, failure
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return UnifrCentralJobs(details)
}
