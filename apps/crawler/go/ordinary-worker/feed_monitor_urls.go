package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"reflect"
	"regexp"
	"sort"
	"strconv"
)

var collisionSourceIdentityPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}$`)

func applyFeedMonitorURLs(ctx context.Context, config map[string]string, jobs []RichMonitorJob, rejected ...*int) ([]RichMonitorJob, error) {
	rules, err := queue.FeedMonitorURLRules(config)
	if err != nil {
		return nil, err
	}
	// Normalize the raw dictionary before content filtering: first position,
	// last content, matching the legacy monitor wrapper.
	ordered := []string{}
	raw := map[string]RichMonitorJob{}
	explicit := map[string]bool{}
	for offset, job := range jobs {
		if rules.HasCollision() && job.SourceIdentity != "" {
			if !collisionSourceIdentityPattern.MatchString(job.SourceIdentity) {
				return nil, queue.ErrProviderClassification
			}
			partition := 0
			if config["crawler_type"] == "rss" {
				partition = offset / 200
			}
			identity := strconv.Itoa(partition) + ":" + job.SourceIdentity
			if explicit[identity] {
				return nil, queue.ErrProviderClassification
			}
			explicit[identity] = true
		}
		if previous, seen := raw[job.URL]; rules.HasCollision() && seen && !reflect.DeepEqual(previous, job) {
			return nil, queue.ErrProviderClassification
		}
		if _, seen := raw[job.URL]; !seen {
			ordered = append(ordered, job.URL)
		}
		raw[job.URL] = job
	}
	if rules.HasCollision() {
		sort.Strings(ordered)
	}
	result := make([]RichMonitorJob, 0, len(ordered))
	ranks := map[string]queue.FeedCollisionRank{}
	filteredSources := 0
	rich, urlsOnly := false, false
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
		if rules.HasCollision() {
			filteredSources++
			if config["crawler_type"] == "rss" && filteredSources > rules.CollisionBufferLimit() {
				return nil, queue.ErrProviderClassification
			}
			if job.URLOnly {
				urlsOnly = true
			} else {
				rich = true
			}
			if rich && urlsOnly {
				return nil, queue.ErrProviderClassification
			}
		}
		u, rank, err := rules.RewriteCollision(job.URL, job.Metadata, job.URLOnly)
		if err != nil {
			return nil, err
		}
		job.URL = u
		if index, exists := indexes[u]; exists {
			previous := result[index]
			if rules.HasCollision() && !job.URLOnly {
				if previous.SourceIdentity != "" && job.SourceIdentity != "" && previous.SourceIdentity != job.SourceIdentity {
					return nil, queue.ErrProviderClassification
				}
				identity := previous.SourceIdentity
				if identity == "" {
					identity = job.SourceIdentity
				}
				if rank.Before(ranks[u]) {
					job.SourceIdentity = identity
					result[index] = job
					ranks[u] = rank
				} else {
					result[index].SourceIdentity = identity
				}
			} else {
				result[index] = job
			}
		} else {
			ranks[u] = rank
			indexes[u] = len(result)
			result = append(result, job)
		}
	}
	return result, nil
}
