package worker

import (
	"context"
	"errors"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
)

func fetchADPDetail(ctx context.Context, client *http.Client, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	o, e := api.ADPDetailRoute(p.SourceURL, p.HTTPAPIConfig)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	raw, response, e := fetchProviderStatusResource(ctx, client, o, o.DetailURL(), nil, http.Header{"Accept": []string{"application/json"}}, 2<<20, nil, true)
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		var f *DiscoveryError
		if errors.As(e, &f) && f.Kind == "body_limit" {
			return nil, nil, executor.ErrEmptyResult
		}
		return nil, nil, e
	}
	_ = response
	d, e := api.Decode(raw)
	if e != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	row, ok := d.Value.(map[string]any)
	if !ok {
		return nil, nil, executor.ErrEmptyResult
	}
	description := api.ADPInlineDescription(row["requisitionDescription"])
	if description == "" {
		if path := api.ADPAttachmentPath(row); path != "" {
			bytes, response, e := fetchProviderStatusResource(ctx, client, o, o.DocumentURL(), nil, o.DocumentHeaders(path), 10<<20, nil, true)
			if errors.As(e, &reserved) {
				return nil, reserved, nil
			}
			if ctx.Err() != nil {
				return nil, nil, ctx.Err()
			}
			if e != nil {
				var f *DiscoveryError
				bounded := errors.As(e, &f) && f.Kind == "body_limit"
				if !bounded && (response == nil || response.status != 404 && response.status != 410) {
					return nil, nil, e
				}
			} else {
				description = api.ADPDocxToHTML(bytes)
			}
		}
	}
	fields, e := api.ADPDetailFields(d, o, description)
	return fields, nil, e
}
func fetchPaylocityDetail(ctx context.Context, client *http.Client, source string) (map[string]any, *policy.Reservation, error) {
	if api.PaylocityDetailRoute(source) != nil {
		return nil, nil, executor.ErrEmptyResult
	}
	raw, _, e := fetchProviderResource(ctx, client, fourthExactResource(source), source, nil, nil, 64<<20)
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		var f *DiscoveryError
		if errors.As(e, &f) && (f.Kind == "http_status" || f.Kind == "provider_gone") {
			return nil, nil, executor.ErrEmptyResult
		}
		return nil, nil, e
	}
	fields, e := api.ParsePaylocityDetail(jsonld.DecodeDocument(raw, ""))
	return fields, nil, e
}
