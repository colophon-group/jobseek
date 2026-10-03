package worker

import (
	"context"
	"errors"
	"sort"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

type GreenhouseInventory struct {
	Jobs        []greenhouse.Job
	Discovered  int
	DropReasons map[string]int
	Truncated   bool
}

// NormalizeGreenhouseInventory implements the default non-streaming Python
// Greenhouse inventory contract, before native 500-row persistence chunks.
// Raw duplicates retain their first dictionary position and last content;
// canonical aliases then retain the last raw dictionary entry's content.
// Truncation suppresses disappearance; it never slices collected postings.
func NormalizeGreenhouseInventory(ctx context.Context, boardURL string, inventory greenhouse.Inventory) (GreenhouseInventory, error) {
	if err := ctx.Err(); err != nil {
		return GreenhouseInventory{}, err
	}
	raw := make(map[string]greenhouse.Job, len(inventory.Jobs))
	order := make([]string, 0, len(inventory.Jobs))
	for _, job := range inventory.Jobs {
		if job.URL == "" {
			return GreenhouseInventory{}, errors.New("Greenhouse inventory contains missing raw URL")
		}
		if _, seen := raw[job.URL]; !seen {
			order = append(order, job.URL)
		}
		raw[job.URL] = job
	}
	result := GreenhouseInventory{Discovered: len(raw), DropReasons: map[string]int{}, Truncated: inventory.Truncated}
	accepted := make(map[string]greenhouse.Job, len(raw))
	for _, source := range order {
		if err := ctx.Err(); err != nil {
			return GreenhouseInventory{}, err
		}
		url := canonicalJobURL(source)
		if reason := classifyJobURL(url, boardURL); reason != "" {
			result.DropReasons[reason]++
			continue
		}
		job := raw[source]
		job.URL = url
		accepted[url] = job
	}
	urls := make([]string, 0, len(accepted))
	for url := range accepted {
		urls = append(urls, url)
	}
	sort.Strings(urls)
	result.Jobs = make([]greenhouse.Job, 0, len(urls))
	for _, url := range urls {
		result.Jobs = append(result.Jobs, accepted[url])
	}
	return result, ctx.Err()
}
