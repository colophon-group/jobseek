package worker

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type pipelinePreparer struct {
	at, failAt int
	cause      error
}

func (p *pipelinePreparer) Prepare(ctx context.Context, _ greenhouse.Job) (*queue.GreenhouseRichContent, error) {
	p.at++
	if p.at == p.failAt {
		return nil, p.cause
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return &queue.GreenhouseRichContent{Fields: queue.GreenhouseRichFields{Titles: []string{}, Locales: []string{"en"}}}, nil
}

type pipelineSink struct {
	chunks      []int
	finished    bool
	invalidated bool
	summary     queue.GreenhouseInventorySummary
	failAt      int
	cause       error
	cancel      context.CancelFunc
}

func (s *pipelineSink) InvalidateInventory() { s.invalidated = true }

func (s *pipelineSink) WriteRichBatch(_ context.Context, batch []queue.GreenhouseRichPosting) (*queue.GreenhouseRichBatchResult, error) {
	if len(s.chunks)+1 == s.failAt {
		return nil, s.cause
	}
	s.chunks = append(s.chunks, len(batch))
	if s.cancel != nil {
		s.cancel()
	}
	return &queue.GreenhouseRichBatchResult{Inserted: len(batch)}, nil
}
func (s *pipelineSink) FinishSuccess(_ context.Context, summary queue.GreenhouseInventorySummary) (*queue.GreenhouseCycleResult, error) {
	s.finished = true
	s.summary = summary
	return &queue.GreenhouseCycleResult{Status: "succeeded"}, nil
}
func pipelineInventory(n int) GreenhouseInventory {
	result := GreenhouseInventory{Discovered: n, Jobs: make([]greenhouse.Job, n), DropReasons: map[string]int{}}
	for i := range result.Jobs {
		result.Jobs[i].URL = fmt.Sprintf("https://example.com/jobs/%d", i)
	}
	return result
}
func TestNativeRichPipelineWholeInventoryAndPartialSummary(t *testing.T) {
	for _, partial := range []bool{false, true} {
		t.Run(fmt.Sprint(partial), func(t *testing.T) {
			inventory := pipelineInventory(1001)
			inventory.Truncated = partial
			inventory.Discovered += 2
			inventory.DropReasons["bare_host"] = 2
			sink := &pipelineSink{}
			preparer := &pipelinePreparer{}
			result, err := PersistGreenhouseInventory(context.Background(), sink, preparer, inventory)
			if err != nil || result.Cycle == nil || result.Batches.Inserted != 1001 || !reflect.DeepEqual(sink.chunks, []int{500, 500, 1}) || !sink.finished || preparer.at != 1001 || sink.summary != (queue.GreenhouseInventorySummary{Discovered: 1003, ProcessingFiltered: 2, Truncated: partial}) {
				t.Fatal("native pipeline lost global inventory or committed-chunk accounting")
			}
		})
	}
}
func TestNativeRichPipelineFailureNeverFinalizesPartialSuccess(t *testing.T) {
	for _, mode := range []string{"prepare_first", "prepare_second", "write_second", "cancel_after_first", "invalid_accounting", "duplicate_identity"} {
		t.Run(mode, func(t *testing.T) {
			inventory := pipelineInventory(1001)
			cause := errors.New("private processing failure")
			sink := &pipelineSink{}
			preparer := &pipelinePreparer{cause: cause}
			ctx := context.Background()
			wantChunks := []int{500}
			switch mode {
			case "prepare_first":
				preparer.failAt = 250
				wantChunks = nil
			case "prepare_second":
				preparer.failAt = 750
			case "write_second":
				sink.failAt = 2
				sink.cause = cause
			case "cancel_after_first":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				defer cancel()
				sink.cancel = cancel
			case "invalid_accounting":
				inventory.DropReasons["invalid"] = 1
				wantChunks = nil
			case "duplicate_identity":
				inventory.Jobs[1000].URL = inventory.Jobs[0].URL
				wantChunks = nil
			}
			result, err := PersistGreenhouseInventory(ctx, sink, preparer, inventory)
			if err == nil || result != nil && result.Cycle != nil || sink.finished || !sink.invalidated || !reflect.DeepEqual(sink.chunks, wantChunks) {
				t.Fatal("failed inventory finalized success or committed partial preparation")
			}
			wantInserted := 0
			for _, n := range wantChunks {
				wantInserted += n
			}
			if wantInserted > 0 && result == nil || result != nil && result.Batches.Inserted != wantInserted {
				t.Fatal("partial inventory lost committed-prefix observations")
			}
		})
	}
}
