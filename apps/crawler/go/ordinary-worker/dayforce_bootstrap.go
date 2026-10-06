package worker

import (
	"context"
	"net/http"
	"net/url"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

func bootstrapDayforce(ctx context.Context, client *http.Client, board api.DayforceBoard, wait func(context.Context, time.Duration) error) (string, api.DayforceSite, *GreenhouseResponse, error) {
	if client == nil || wait == nil {
		return "", api.DayforceSite{}, nil, api.ErrOptions
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	target := board.ListingURL()
	page, observed, e := fetchFifthListing(ctx, &sealed, board, target, map[int]bool{202: true, 403: true, 429: true}, 1_000_000, true, wait)
	if e != nil && observed != nil && (observed.status == 301 || observed.status == 302 || observed.status == 307 || observed.status == 308) {
		redirect, err := board.LocalizedRedirect(target, observed.location)
		if err == nil {
			target = redirect
			page, observed, e = fetchFifthListing(ctx, &sealed, board, target, map[int]bool{202: true, 403: true, 429: true}, 1_000_000, true, wait)
		}
	}
	if e != nil {
		if observed != nil && (observed.status == 404 || observed.status == 410) {
			e = &DiscoveryError{Kind: "provider_gone", Status: observed.status}
		}
		return "", api.DayforceSite{}, observed, e
	}
	classified, e := dom.ClassifyDocument(page, dom.Object{}, target)
	if e != nil || classified["classification"] == "challenge" {
		return "", api.DayforceSite{}, observed, &DiscoveryError{Kind: "bot_challenge"}
	}
	site, e := api.DayforceExtractSite(page, board)
	if e != nil {
		return "", api.DayforceSite{}, observed, e
	}
	u, _ := url.Parse(target)
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) == 3 && !strings.EqualFold(parts[0], site.Culture) {
		return "", api.DayforceSite{}, observed, api.ErrInventory
	}
	if site.Disabled {
		observed.providerDisabled = true
		return "", api.DayforceSite{}, observed, &DiscoveryError{Kind: "provider_gone"}
	}
	return page, site, observed, nil
}
