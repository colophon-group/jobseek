package worker

import (
	"context"
	"errors"
	"sort"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

type RichMonitorJob struct {
	URL                                  string
	SourceIdentity                       string
	Title, Description                   *string
	Locations                            []string
	Language                             any
	LocalizedTitles, LocalizationLocales []string
	DatePosted                           any
	Metadata                             map[string]any
	Extras                               map[string]any
	EmploymentType, JobLocationType      any
}

type GreenhouseInventory struct {
	Jobs        []RichMonitorJob
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
	jobs := make([]RichMonitorJob, len(inventory.Jobs))
	for i, job := range inventory.Jobs {
		jobs[i] = RichMonitorJob{URL: job.URL, Title: job.Title, Description: job.Description, Locations: job.Locations, Language: job.Language, DatePosted: job.DatePosted, Metadata: job.Metadata}
	}
	return NormalizeRichInventory(ctx, boardURL, jobs, inventory.Truncated)
}

func NormalizeRichInventory(ctx context.Context, boardURL string, jobs []RichMonitorJob, truncated bool) (GreenhouseInventory, error) {
	if err := ctx.Err(); err != nil {
		return GreenhouseInventory{}, err
	}
	raw := make(map[string]RichMonitorJob, len(jobs))
	order := make([]string, 0, len(jobs))
	for _, job := range jobs {
		if job.URL == "" {
			return GreenhouseInventory{}, errors.New("Greenhouse inventory contains missing raw URL")
		}
		if _, seen := raw[job.URL]; !seen {
			order = append(order, job.URL)
		}
		raw[job.URL] = job
	}
	result := GreenhouseInventory{Discovered: len(raw), DropReasons: map[string]int{}, Truncated: truncated}
	accepted := make(map[string]RichMonitorJob, len(raw))
	explicit := map[string]bool{}
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
		if job.SourceIdentity != "" {
			if explicit[job.SourceIdentity] {
				return GreenhouseInventory{}, errors.New("repeated explicit source identity")
			}
			explicit[job.SourceIdentity] = true
			if prior, ok := accepted[url]; ok && prior.SourceIdentity != job.SourceIdentity {
				return GreenhouseInventory{}, errors.New("outbound URL belongs to multiple explicit identities")
			}
		}
		job.URL = url
		accepted[url] = job
	}
	urls := make([]string, 0, len(accepted))
	for url := range accepted {
		urls = append(urls, url)
	}
	sort.Strings(urls)
	result.Jobs = make([]RichMonitorJob, 0, len(urls))
	for _, url := range urls {
		result.Jobs = append(result.Jobs, accepted[url])
	}
	return result, ctx.Err()
}
