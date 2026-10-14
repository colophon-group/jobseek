package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/url"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// These providers share the sealed HTTP transport and canonical writer, while
// retaining their original fallback, finite retry and response identity rules.
func fetchLastStaticDetail(ctx context.Context, client *http.Client, p q.WorkdayDetailProfile, wait func(context.Context, time.Duration) error) (map[string]any, *policy.Reservation, error) {
	provider := map[string]string{"onlyfy.public-detail/v1": "onlyfy", "tupu360.public-detail/v1": "tupu360", "phuketall.public-detail/v1": "phuketall"}[p.Profile]
	o, err := api.StaticProviderDetailOptionsForSource(provider, p.SourceURL)
	if err != nil || client == nil || wait == nil {
		return nil, nil, q.ErrConfiguration
	}
	if provider == "onlyfy" {
		language, _ := p.HTTPAPIConfig["language"].(string)
		candidate, listing, handle, err := api.OnlyfyDetailURLs(p.SourceURL, language)
		if err != nil || candidate != p.Endpoint {
			return nil, nil, q.ErrConfiguration
		}
		get := func(endpoint string) ([]byte, int, *policy.Reservation, error) {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			defer cancel()
			raw, status, _, reserved, err := staticProviderDetailPage(requestCtx, client, endpoint, 25_000_000)
			return raw, status, reserved, err
		}
		raw, status, reserved, err := get(candidate)
		if err != nil || reserved != nil {
			return nil, reserved, err
		}
		if status < 200 || status >= 300 {
			return nil, nil, &DiscoveryError{Kind: "http_status", Status: status}
		}
		out, err := api.ParseOnlyfyDetail(string(raw), p.HTTPAPIConfig)
		if err != nil {
			return nil, nil, err
		}
		if out["description"] == nil || out["description"] == "" {
			raw, status, reserved, err = get(p.SourceURL)
			if err != nil || reserved != nil {
				return nil, reserved, err
			}
			if status == 200 {
				fallback, err := api.ParseOnlyfyDetail(string(raw), p.HTTPAPIConfig)
				if err != nil {
					return nil, nil, err
				}
				out["description"] = fallback["description"]
				if out["locations"] == nil {
					out["locations"] = fallback["locations"]
				}
			}
		}
		if out["locations"] == nil {
			raw, status, reserved, err = get(listing)
			if err != nil || reserved != nil {
				return nil, reserved, err
			}
			if status == 200 {
				if locations := api.OnlyfyListingLocation(string(raw), handle); len(locations) != 0 {
					out["locations"] = locations
				}
			}
		}
		return out, nil, nil
	}
	if o.Endpoint != p.Endpoint {
		return nil, nil, q.ErrConfiguration
	}
	current := o.Endpoint
	employer, job := api.PhuketDetailIdentity(current)
	for hop := 0; hop <= 2; hop++ {
		raw, status, location, reserved, err := lastStaticRetryPage(ctx, client, current, provider == "phuketall", wait)
		if err != nil || reserved != nil {
			return nil, reserved, err
		}
		if provider == "phuketall" && (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
			base, _ := url.Parse(current)
			target, parseErr := base.Parse(location)
			if parseErr != nil || location == "" || hop == 2 {
				return nil, nil, &DiscoveryError{Kind: "redirect"}
			}
			e, j := api.PhuketDetailIdentity(target.String())
			if e != employer || j != job {
				return nil, nil, &DiscoveryError{Kind: "redirect"}
			}
			current = target.String()
			continue
		}
		if status != 200 {
			return nil, nil, &DiscoveryError{Kind: "http_status", Status: status}
		}
		if provider == "phuketall" {
			e, j := api.PhuketDocumentIdentity(string(raw))
			if e != employer || j != job {
				return nil, nil, q.ErrObservation
			}
			out, err := api.ParsePhuketDetail(string(raw))
			return out, nil, err
		}
		out, err := api.ParseTupuDetail(string(raw))
		if err != nil {
			return nil, nil, err
		}
		metadata, _ := out["metadata"].(map[string]any)
		allowListing, _ := p.HTTPAPIConfig["listing_enrichment"].(bool)
		if metadata["position_id"] != api.TupuDetailIdentity(p.SourceURL) || out["title"] == nil || out["description"] == nil || out["locations"] == nil && !allowListing {
			return nil, nil, q.ErrObservation
		}
		return out, nil, nil
	}
	return nil, nil, q.ErrObservation
}

func lastStaticRetryPage(ctx context.Context, client *http.Client, endpoint string, single bool, wait func(context.Context, time.Duration) error) ([]byte, int, string, *policy.Reservation, error) {
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		var requests []api.Request
		if single {
			requests = []api.Request{{Method: "GET", URL: endpoint}}
		}
		location := ""
		raw, status, _, reserved, err := staticProviderDetailPageLocation(requestCtx, client, endpoint, 2<<20, &location, requests...)
		cancel()
		if reserved != nil {
			return nil, status, location, reserved, nil
		}
		if ctx.Err() != nil {
			return nil, status, location, nil, ctx.Err()
		}
		if err == nil && status == 200 && len(raw) != 0 {
			return raw, status, location, nil, nil
		}
		retry := err != nil || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status < 600
		var bounded *DiscoveryError
		if errors.As(err, &bounded) && (bounded.Kind == "body_limit" || bounded.Kind == "redirect") {
			retry = false
		}
		if !retry {
			return raw, status, location, nil, err
		}
		if err == nil {
			err = &DiscoveryError{Kind: "http_status", Status: status}
		}
		if attempt == 2 {
			return nil, status, location, nil, err
		}
		delay := time.Duration(float64(500*time.Millisecond*time.Duration(1<<attempt)) * (.5 + rand.Float64()))
		if err := wait(ctx, delay); err != nil {
			return nil, status, location, nil, err
		}
	}
	return nil, 0, "", nil, q.ErrObservation
}
