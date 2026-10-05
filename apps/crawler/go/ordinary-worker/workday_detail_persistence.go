package worker

import (
	"context"
	"encoding/json"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
	"github.com/jackc/pgx/v5"
)

// PersistWorkdayDetail prepares already fetched Workday content with the same
// in-process detail processor and SQL writer as B0. The opaque canonical
// observation must come from the installed attempt. This function neither
// selects nor fetches work; the runner owns failure and publisher handling.
func PersistWorkdayDetail(ctx context.Context, authority *queue.Authority, detail *queue.CurrentWorkdayDetail, processor *executor.Processor, content workday.DetailContent) (*queue.Receipt, error) {
	if authority == nil || detail == nil || processor == nil || !detail.Schedulable || detail.PublisherReserved {
		return nil, claimRunError("detail_startup", queue.ErrConfiguration)
	}
	// Use the extractor's established JobContent JSON boundary, including null
	// scalar fields and array values, instead of a second field-normalization
	// policy. The processor performs HTML, locale, location and taxonomy work.
	body, err := json.Marshal(content)
	if err != nil {
		return nil, claimRunError("detail_preparation", err)
	}
	var values map[string]any
	if err := json.Unmarshal(body, &values); err != nil {
		return nil, claimRunError("detail_preparation", err)
	}
	return PersistDetailContent(ctx, authority, detail, processor, values)
}

func PersistDetailContent(ctx context.Context, authority *queue.Authority, detail *queue.CurrentWorkdayDetail, processor *executor.Processor, values map[string]any) (*queue.Receipt, error) {
	if authority == nil || detail == nil || processor == nil || !detail.Schedulable || detail.PublisherReserved {
		return nil, claimRunError("detail_startup", queue.ErrConfiguration)
	}
	var config map[string]any
	var existing *executor.EnrichSnapshot
	if fields := detail.Profile().EnrichmentFields; len(fields) > 0 {
		selected := make([]any, len(fields))
		for n, field := range fields {
			selected[n] = field
		}
		config = map[string]any{"enrich": selected}
		existing = &executor.EnrichSnapshot{Titles: detail.Titles, LocationIDs: detail.LocationIDs, EmploymentType: detail.EmploymentType}
	}
	prepared, err := processor.Prepare(ctx, values, config, existing)
	if err != nil {
		return nil, claimRunError("detail_preparation", err)
	}
	receipt, err := authority.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
		save := executor.SaveContent
		if prepared.Enrich {
			save = executor.SaveEnrichment
		}
		_, err := save(ctx, tx, detail.PostingID(), prepared.Fields, prepared.Description)
		return err
	})
	if err != nil {
		return nil, claimRunError("detail_write", err)
	}
	return receipt, nil
}
