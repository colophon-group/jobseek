package worker

import (
	"context"
	"errors"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

// DiscoverWorkdayInventory uses the existing process-owned HTTP transport for
// URL-only discovery. The owned claim runner must persist URLs and schedule
// separate detail work before finalizing a monitor cycle.
func DiscoverWorkdayInventory(ctx context.Context, http *VerifiedDirectHTTP, config workday.InventoryConfig) (workday.InventoryResult, error) {
	if http == nil || http.client == nil {
		return workday.InventoryResult{}, &DiscoveryError{Kind: "invalid_configuration"}
	}
	poster, err := workday.NewInventoryPoster(config, http.client)
	if err != nil {
		return workday.InventoryResult{}, &DiscoveryError{Kind: "invalid_configuration", cause: err}
	}
	inventory, err := workday.DiscoverInventory(ctx, config, poster.GetRobots, poster.Post)
	if err == nil {
		return inventory, nil
	}
	if ctx.Err() != nil {
		return workday.InventoryResult{}, ctx.Err()
	}
	var reservation *workday.ReservationError
	if errors.As(err, &reservation) {
		return workday.InventoryResult{}, &DiscoveryError{Kind: "publisher_reserved", Status: 200, cause: err}
	}
	var failed *workday.FetchError
	if errors.As(err, &failed) {
		return workday.InventoryResult{}, &DiscoveryError{Kind: "http_status", Status: failed.Status, cause: err}
	}
	return workday.InventoryResult{}, &DiscoveryError{Kind: "invalid_inventory", cause: err}
}

// FetchWorkdayDetail uses the sealed process transport for one separately
// claimed detail. Persistence and enrichment remain with the owned runner.
func FetchWorkdayDetail(ctx context.Context, http *VerifiedDirectHTTP, rawURL string, aliases []string) (workday.DetailFetchResult, error) {
	if http == nil || http.client == nil {
		return workday.DetailFetchResult{}, &DiscoveryError{Kind: "invalid_configuration"}
	}
	return workday.FetchDetailWithClient(ctx, rawURL, aliases, http.client)
}
