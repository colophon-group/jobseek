package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func applyFeedMonitorURLs(ctx context.Context, config map[string]string, jobs []RichMonitorJob) ([]RichMonitorJob, error) {
	rules, err := queue.FeedMonitorURLRules(config)
	if err != nil {
		return nil, err
	}
	result := make([]RichMonitorJob, 0, len(jobs))
	indexes := map[string]int{}
	for _, job := range jobs {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		u, keep, err := rules.Apply(job.URL)
		if err != nil {
			return nil, err
		}
		if !keep {
			continue
		}
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
