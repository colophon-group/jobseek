package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"
)

func discoverRipplingInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.RipplingOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || p.Provider != "rippling" || p.Profile != "rippling.v1-urls/v1" || p.Endpoint != o.ListingURL() || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	op := *client
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	raw, response, e := fetchProviderStatusResource(ctx, &op, o, o.ListingURL(), nil, nil, 64<<20, nil, true)
	out.Response = response
	if e != nil {
		var f *DiscoveryError
		if errors.As(e, &f) && f.Kind == "provider_gone" {
			e = &DiscoveryError{Kind: "http_status", Status: f.Status}
		}
		return out, e
	}
	d, e := api.Decode(raw)
	if e != nil {
		return out, e
	}
	urls, truncated, e := api.RipplingListing(d, o)
	if e != nil {
		return out, e
	}
	out.Truncated = truncated
	for _, s := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: s, URLOnly: true})
	}
	return out, nil
}
func fetchPaycomBootstrap(ctx context.Context, client *http.Client, o api.PaycomOptions, wait func(context.Context, time.Duration) error) (api.PaycomBootstrap, *GreenhouseResponse, error) {
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		body, response, e := fetchProviderResource(ctx, client, o, o.PortalURL(), nil, nil, 64<<20)
		observed = response
		if ctx.Err() != nil {
			return api.PaycomBootstrap{}, observed, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			return api.PaycomBootstrap{}, observed, e
		}
		status := 0
		if observed != nil {
			status = observed.status
		}
		if e == nil && status == 200 {
			text := jsonld.DecodeDocument(body, "")
			if strings.Contains(strings.ToLower(text), "job board does not exist or is unavailable") {
				observed.providerDisabled = true
				return api.PaycomBootstrap{}, observed, &DiscoveryError{Kind: "provider_gone", Status: 200}
			}
			b, e := api.PaycomExtractBootstrap(text, o)
			return b, observed, e
		}
		if status == 404 || status == 410 {
			return api.PaycomBootstrap{}, observed, &DiscoveryError{Kind: "provider_gone", Status: status}
		}
		if status != 0 && status != 408 && status != 425 && status != 429 && (status < 500 || status > 599) {
			return api.PaycomBootstrap{}, observed, &DiscoveryError{Kind: "http_status", Status: status}
		}
		if attempt == 2 {
			break
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if e := wait(ctx, delay); e != nil {
			return api.PaycomBootstrap{}, observed, e
		}
	}
	return api.PaycomBootstrap{}, observed, &DiscoveryError{Kind: "bootstrap_failed"}
}
func discoverPaycomInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverPaycomInventoryWithWait(ctx, client, p, config, pauseRich)
}
func discoverPaycomInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.PaycomOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || wait == nil || p.Provider != "paycom" || p.Profile != "paycom.preview-items/v1" || p.Endpoint != o.PortalURL() || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	op := *client
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	b, response, e := fetchPaycomBootstrap(ctx, &op, o, wait)
	out.Response = response
	if e != nil {
		return out, e
	}
	rawSeen := 0
	expected := int64(-1)
	invalid, duplicates := 0, 0
	seen := map[string]bool{}
	fetch := func() (int64, []any, error) {
		payload, e := api.PaycomSearchPayload(rawSeen)
		if e != nil {
			return 0, nil, e
		}
		d, response, e := fetchSecondaryJSONPage(ctx, &op, b, b.SearchURL(), payload, b.Headers, nil, wait)
		out.Response = response
		if e != nil {
			return 0, nil, e
		}
		return api.PaycomPage(d)
	}
	for page := 0; page < 500; page++ {
		total, rows, e := fetch()
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		if expected < 0 {
			expected = total
		} else if total != expected {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		if len(rows) == 0 && int64(rawSeen) < total {
			total, rows, e = fetch()
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			if total != expected || len(rows) == 0 {
				return RichDiscovery{Response: out.Response}, api.ErrInventory
			}
		}
		if len(rows) > 100 || int64(rawSeen+len(rows)) > total {
			return RichDiscovery{Response: out.Response}, api.ErrInventory
		}
		for _, raw := range rows {
			rawSeen++
			fields := api.PaycomJobFields(raw, o)
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
			kind := ""
			if s, ok := fields["job_location_type"].(string); ok {
				kind = enrichment.NormalizeJobLocationType(s)
			}
			if kind == "" {
				if locs, ok := fields["locations"].([]string); ok && len(locs) > 0 && strings.Contains(strings.ToLower(locs[0]), "remote") {
					kind = "remote"
				}
			}
			fields["job_location_type"] = nil
			if kind != "" {
				fields["job_location_type"] = kind
			}
			job, e := secondaryRichJob(fields)
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			job.Hybrid = true
			out.Jobs = append(out.Jobs, job)
		}
		if int64(rawSeen) >= total || rawSeen >= 50000 {
			if expected > 0 && len(seen) == 0 {
				return RichDiscovery{Response: out.Response}, api.ErrInventory
			}
			out.Truncated = invalid > 0 || duplicates > 0 || int64(rawSeen) != expected || expected > 50000
			return out, nil
		}
	}
	out.Truncated = true
	return out, nil
}
