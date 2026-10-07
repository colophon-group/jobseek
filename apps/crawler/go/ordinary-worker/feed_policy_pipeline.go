package worker

import (
	"context"
	"errors"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// RSS applies shared policy to each original 200-job stream batch. DOM uses
// one complete discovered list. A late classification error retains committed
// earlier RSS batches; provider-boundary rejects retain accepted writes but
// prevent all terminal empty/gone processing.
func writeFeedPolicyInventory(ctx context.Context, sink GreenhouseSink, preparer RichPreparer, config map[string]string, discovery RichDiscovery) (*GreenhouseProcessingResult, queue.GreenhouseInventorySummary, int, error) {
	result := &GreenhouseProcessingResult{}
	summary := queue.GreenhouseInventorySummary{Truncated: discovery.Truncated, MetadataUpdates: discovery.MetadataUpdates}
	rejected := 0
	explicit := map[string]bool{}
	batchSize := max(1, len(discovery.Jobs))
	if config["crawler_type"] == "rss" {
		batchSize = 200
	}
	for offset := 0; offset < len(discovery.Jobs); offset += batchSize {
		jobs, err := applyFeedMonitorURLs(ctx, config, discovery.Jobs[offset:min(offset+batchSize, len(discovery.Jobs))], &rejected)
		if err != nil {
			return result, summary, rejected, err
		}
		inventory, err := NormalizeRichInventory(ctx, config["board_url"], jobs, false)
		if err != nil {
			return result, summary, rejected, err
		}
		for _, job := range inventory.Jobs {
			if job.SourceIdentity != "" {
				if explicit[job.SourceIdentity] {
					return result, summary, rejected, errors.New("repeated explicit identity across feed batches")
				}
				explicit[job.SourceIdentity] = true
			}
		}
		summary.Discovered += inventory.Discovered
		for _, count := range inventory.DropReasons {
			summary.ProcessingFiltered += count
		}
		written, err := WriteGreenhouseInventory(ctx, sink, preparer, inventory)
		if written != nil {
			batch := written.Batches
			result.Batches.Inserted += batch.Inserted
			result.Batches.Touched += batch.Touched
			result.Batches.Relisted += batch.Relisted
			result.Batches.Foreign += batch.Foreign
			result.Batches.ForeignRelisted += batch.ForeignRelisted
			result.Batches.Deduplicated += batch.Deduplicated
		}
		if err != nil {
			return result, summary, rejected, err
		}
	}
	return result, summary, rejected, nil
}
