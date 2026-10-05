package worker

import (
	"context"
	"errors"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type RichPreparer interface {
	Prepare(context.Context, RichMonitorJob) (*queue.GreenhouseRichContent, error)
}

// NativeRichPreparer reuses the same in-process models/lookup/location pipeline
// as the native B0 executor, with its separate rich-monitor (not detail) policy.
type NativeRichPreparer struct{ Processor *executor.Processor }

func (p NativeRichPreparer) Prepare(ctx context.Context, job RichMonitorJob) (*queue.GreenhouseRichContent, error) {
	if p.Processor == nil {
		return nil, errors.New("native rich processor unavailable")
	}
	prepared, err := p.Processor.PrepareRichMonitor(ctx, executor.RichMonitorContent{Title: job.Title, Description: job.Description, Locations: job.Locations, Language: job.Language, LocalizedTitles: job.LocalizedTitles, LocalizationLocales: job.LocalizationLocales, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType, Extras: job.Extras})
	if err != nil {
		return nil, err
	}
	content := &queue.GreenhouseRichContent{Fields: queue.GreenhouseRichFields(prepared.Fields), Enrich: prepared.Enrich}
	if prepared.Description != nil {
		description := queue.GreenhouseRichDescription(*prepared.Description)
		content.Description = &description
	}
	return content, nil
}

type GreenhouseSink interface {
	InvalidateInventory()
	WriteRichBatch(context.Context, []queue.GreenhouseRichPosting) (*queue.GreenhouseRichBatchResult, error)
	FinishSuccess(context.Context, queue.GreenhouseInventorySummary) (*queue.GreenhouseCycleResult, error)
}

type GreenhouseProcessingResult struct {
	Cycle   *queue.GreenhouseCycleResult
	Batches queue.GreenhouseRichBatchResult
}

// WriteGreenhouseInventory prepares each 500-row chunk before its owned atomic
// write. It leaves terminal absence handling to the caller after the complete
// stream succeeds. Earlier committed chunks survive a later failure.
func WriteGreenhouseInventory(ctx context.Context, sink GreenhouseSink, preparer RichPreparer, inventory GreenhouseInventory) (*GreenhouseProcessingResult, error) {
	if sink == nil {
		return nil, errors.New("native inventory sink unavailable")
	}
	completed := false
	defer func() {
		if !completed {
			sink.InvalidateInventory()
		}
	}()
	if preparer == nil || inventory.Discovered < len(inventory.Jobs) {
		return nil, errors.New("invalid native inventory pipeline")
	}
	filtered := 0
	for reason, count := range inventory.DropReasons {
		if count < 0 || (reason != "invalid" && reason != "bare_host" && reason != "board_homepage") || count > inventory.Discovered-len(inventory.Jobs)-filtered {
			return nil, errors.New("invalid native URL drop accounting")
		}
		filtered += count
	}
	seen := make(map[string]bool, len(inventory.Jobs))
	for _, job := range inventory.Jobs {
		if job.URL == "" || seen[job.URL] {
			return nil, errors.New("native inventory identities are not normalized")
		}
		seen[job.URL] = true
	}
	result := &GreenhouseProcessingResult{}
	for start := 0; start < len(inventory.Jobs); start += 500 {
		end := min(start+500, len(inventory.Jobs))
		batch := make([]queue.GreenhouseRichPosting, 0, end-start)
		for _, job := range inventory.Jobs[start:end] {
			if err := ctx.Err(); err != nil {
				return result, err
			}
			content, err := preparer.Prepare(ctx, job)
			if err != nil {
				return result, claimRunError("preparation", err)
			}
			if content == nil {
				return result, errors.New("native rich preparation returned no content")
			}
			batch = append(batch, queue.GreenhouseRichPosting{URL: job.URL, Content: content})
		}
		counts, err := sink.WriteRichBatch(ctx, batch)
		if err != nil {
			return result, claimRunError("posting_write", err)
		}
		if counts == nil {
			return result, errors.New("native batch returned no committed accounting")
		}
		result.Batches.Inserted += counts.Inserted
		result.Batches.Touched += counts.Touched
		result.Batches.Relisted += counts.Relisted
		result.Batches.Foreign += counts.Foreign
		result.Batches.ForeignRelisted += counts.ForeignRelisted
		result.Batches.Deduplicated += counts.Deduplicated
		if len(counts.Details) > 0 {
			publisher, ok := sink.(interface {
				EnqueueURLDetail(context.Context, queue.URLOnlyDetail) (bool, error)
			})
			if !ok {
				return result, errors.New("native rich detail publisher unavailable")
			}
			for _, detail := range counts.Details {
				if _, err := publisher.EnqueueURLDetail(ctx, detail); err != nil {
					return result, claimRunError("detail_enqueue", err)
				}
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	completed = true
	return result, nil
}

// Streamed providers use the same chunk writer, then prove complete inventory
// before a single terminal disappearance/schedule update. Existing providers
// retain their non-streaming wrapper and failure invalidation.
func PersistGreenhouseInventory(ctx context.Context, sink GreenhouseSink, preparer RichPreparer, inventory GreenhouseInventory) (*GreenhouseProcessingResult, error) {
	result, err := WriteGreenhouseInventory(ctx, sink, preparer, inventory)
	if err != nil {
		return result, err
	}
	filtered := 0
	for _, count := range inventory.DropReasons {
		filtered += count
	}
	cycle, err := sink.FinishSuccess(ctx, queue.GreenhouseInventorySummary{Discovered: inventory.Discovered, ProcessingFiltered: filtered, Truncated: inventory.Truncated})
	if err != nil {
		sink.InvalidateInventory()
		return result, claimRunError("finalization", err)
	}
	if cycle == nil {
		sink.InvalidateInventory()
		return result, errors.New("native inventory returned no terminal result")
	}
	result.Cycle = cycle
	return result, nil
}
