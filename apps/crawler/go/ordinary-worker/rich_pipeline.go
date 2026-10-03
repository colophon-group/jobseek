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
	prepared, err := p.Processor.PrepareRichMonitor(ctx, executor.RichMonitorContent{Title: job.Title, Description: job.Description, Locations: job.Locations, Language: job.Language, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType})
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

// PersistGreenhouseInventory prepares an entire 500-row chunk before its owned
// atomic write. Only a successfully prepared/written whole inventory may reach
// terminal absence handling. Earlier committed chunks survive a later failure;
// the caller must use failure/reservation handling or leave recovery authority.
func PersistGreenhouseInventory(ctx context.Context, sink GreenhouseSink, preparer RichPreparer, inventory GreenhouseInventory) (*GreenhouseProcessingResult, error) {
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
				return result, err
			}
			if content == nil {
				return result, errors.New("native rich preparation returned no content")
			}
			batch = append(batch, queue.GreenhouseRichPosting{URL: job.URL, Content: content})
		}
		counts, err := sink.WriteRichBatch(ctx, batch)
		if err != nil {
			return result, err
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
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	cycle, err := sink.FinishSuccess(ctx, queue.GreenhouseInventorySummary{Discovered: inventory.Discovered, ProcessingFiltered: filtered, Truncated: inventory.Truncated})
	if err != nil {
		return result, err
	}
	if cycle == nil {
		return result, errors.New("native inventory returned no terminal result")
	}
	completed = true
	result.Cycle = cycle
	return result, nil
}
