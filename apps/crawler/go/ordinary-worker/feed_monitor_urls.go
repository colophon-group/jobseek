package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func applyFeedMonitorURLs(ctx context.Context, config map[string]string, jobs []RichMonitorJob, rejected ...*int) ([]RichMonitorJob, error) {
	rules, err := queue.FeedMonitorURLRules(config)
	if err != nil {
		return nil, err
	}
	// Normalize the raw dictionary before content filtering: first position,
	// last content, matching the legacy monitor wrapper.
	ordered := []string{}
	raw := map[string]RichMonitorJob{}
	for _, job := range jobs {
		if _, seen := raw[job.URL]; !seen {
			ordered = append(ordered, job.URL)
		}
		raw[job.URL] = job
	}
	result := make([]RichMonitorJob, 0, len(ordered))
	indexes := map[string]int{}
	for _, source := range ordered {
		job := raw[source]
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		keep, err := rules.FilterURL(job.URL)
		if err != nil {
			return nil, err
		}
		if !keep {
			continue
		}
		if rules.JobFilter != nil && !job.URLOnly {
			text, err := feedJobFilterText(job, rules.JobFilter.Field)
			if err != nil {
				return nil, err
			}
			keep, err = rules.JobFilter.Matches(text)
			if err != nil {
				return nil, err
			}
			if !keep {
				continue
			}
		}
		allowed, err := rules.ProviderAllows(job.URL)
		if err != nil {
			return nil, err
		}
		if !allowed {
			if len(rejected) > 0 && rejected[0] != nil {
				*rejected[0]++
			}
			continue
		}
		u := rules.Rewrite(job.URL)
		job.URL = u
		if index, exists := indexes[u]; exists {
			result[index] = job
		} else {
			indexes[u] = len(result)
			result = append(result, job)
		}
	}
	return result, nil
}
