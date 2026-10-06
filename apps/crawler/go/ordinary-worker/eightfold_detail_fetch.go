package worker

import (
	"context"
	"errors"
	"net/http"
	"reflect"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func fetchEightfoldDetail(ctx context.Context, client *http.Client, source string, config map[string]any) (map[string]any, *policy.Reservation, error) {
	if !jsonld.ValidPublicEndpoint(source) || client == nil {
		return nil, nil, queue.ErrConfiguration
	}
	route, routeErr := api.EightfoldDetailRouteForSource(source)
	if routeErr != nil {
		route = api.EightfoldDetailRoute{SourceURL: source}
	}
	content := map[string]any{"title": nil, "description": nil, "locations": nil, "employment_type": nil, "job_location_type": nil, "date_posted": nil, "base_salary": nil, "language": nil, "extras": nil, "metadata": nil}
	raw, _, e := fetchProviderResource(ctx, client, route, source, nil, nil, 64<<20)
	if ctx.Err() != nil {
		return nil, nil, ctx.Err()
	}
	var reserved *policy.Reservation
	if errors.As(e, &reserved) {
		return nil, reserved, nil
	}
	if e == nil {
		content, e = jsonld.Parse(source, raw, config)
		if e != nil {
			return nil, nil, e
		}
	}
	title, e := executor.CoerceText(content["title"])
	if e != nil {
		return nil, nil, e
	}
	if title != nil && *title != "" {
		return content, nil, nil
	}
	if routeErr == nil {
		// Only the HTML request opts into redirects in the reference scraper.
		apiClient := *client
		apiClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
		raw, _, e = fetchProviderResource(ctx, &apiClient, route, route.APIURL, nil, http.Header{"Accept": []string{"application/json"}}, 64<<20)
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		if errors.As(e, &reserved) {
			return nil, reserved, nil
		}
		if e == nil {
			d, e := api.Decode(raw)
			if e == nil {
				if value, ok := d.Value.(map[string]any); ok {
					fallback, e := d.EightfoldDetailFields(value)
					if e != nil {
						return nil, nil, e
					}
					fbTitle, e := executor.CoerceText(fallback["title"])
					if e != nil {
						return nil, nil, e
					}
					fbDescription, e := executor.CoerceText(fallback["description"])
					if e != nil {
						return nil, nil, e
					}
					if fbTitle != nil && *fbTitle != "" || fbDescription != nil && *fbDescription != "" {
						for key, value := range fallback {
							if key == "metadata" || key == "extras" {
								if fields, ok := value.(map[string]any); ok && len(fields) > 0 {
									merged := map[string]any{}
									for k, v := range fields {
										merged[k] = v
									}
									if original, ok := content[key].(map[string]any); ok {
										for k, v := range original {
											merged[k] = v
										}
									}
									content[key] = merged
								}
								continue
							}
							current := content[key]
							if current == nil || reflect.TypeOf(current).Kind() == reflect.Slice && reflect.ValueOf(current).Len() == 0 {
								content[key] = value
							}
						}
					}
				}
			}
		}
	}
	if len(content) == 0 {
		return nil, nil, executor.ErrEmptyResult
	}
	return content, nil, nil
}
