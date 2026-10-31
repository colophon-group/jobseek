package worker

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealMonitorCommittedProgressKeepsLeaseBeyondInitialBudget(t *testing.T) {
	f := privateRichPipelineFixture(t, "lever", `{"token":"fixture","scraper_type":"skip"}`)
	claim, _ := claimFixture(t, f)
	c := fixtureLoopConfig()
	c.monitorProgressTimeout = true
	c.taskTimeout = 500 * time.Millisecond
	c.heartbeat = 30 * time.Millisecond
	c.cancellationGrace = time.Second
	var beats atomic.Int64
	started := time.Now()
	result, err := runLeasedClaim(context.Background(), c, runtimeServices{
		heartbeat: func(ctx context.Context, claim *queue.Claim) error { beats.Add(1); return f.a.Heartbeat(ctx, claim) },
		execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
			cycle, err := f.a.BeginGreenhouseCycle(ctx, claim)
			if err != nil {
				return nil, err
			}
			for i := 0; i < 8; i++ {
				if !waitRuntime(ctx, 100*time.Millisecond) {
					return nil, ctx.Err()
				}
				inventory := pipelineInventory(1)
				inventory.Jobs[0].URL = fmt.Sprintf("https://example.com/progress/%s/%d", f.company, i)
				if _, err := WriteGreenhouseInventory(ctx, cycle, &pipelinePreparer{}, inventory); err != nil {
					return nil, err
				}
			}
			terminal, err := cycle.FinishSuccess(ctx, queue.GreenhouseInventorySummary{Discovered: 8})
			if err != nil {
				return nil, err
			}
			if err := settleClaim(ctx, func() error { return f.a.Settle(ctx, claim, terminal.Receipt) }); err != nil {
				return nil, err
			}
			return &GreenhouseClaimResult{Cycle: terminal, Settled: true}, nil
		},
	}, claim, newRuntimeMetrics(1, c.stallTimeout))
	if err != nil || result == nil || !result.Settled || time.Since(started) <= c.taskTimeout || beats.Load() < 2 {
		t.Fatal("committed progress lost its healthy owned lease", err)
	}
	var postings, failures int
	var success, future bool
	if err := f.pg.QueryRow(context.Background(), `SELECT (SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url LIKE '%/progress/%'),consecutive_failures,last_success_at IS NOT NULL,next_check_at>now() FROM job_board WHERE id=$1::uuid`, f.board).Scan(&postings, &failures, &success, &future); err != nil || postings != 8 || failures != 0 || !success || !future {
		t.Fatal("renewable monitor lost canonical output or success schedule")
	}
	assertRichDeadlineAndLease(t, f, "lever")
}

func TestRealMonitorInactivityStillFailsAndReschedulesOnce(t *testing.T) {
	f := privateRichPipelineFixture(t, "lever", `{"token":"fixture","scraper_type":"skip"}`)
	claim, circuits := claimFixture(t, f)
	client := richPipelineHTTP(t, func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
	c := fixtureLoopConfig()
	c.monitorProgressTimeout = true
	c.taskTimeout = 250 * time.Millisecond
	c.cancellationGrace = time.Second
	c.heartbeat = time.Hour
	result, err := runLeasedClaim(context.Background(), c, runtimeServices{heartbeat: f.a.Heartbeat,
		execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
			return RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
		},
	}, claim, newRuntimeMetrics(1, c.stallTimeout))
	if !errors.Is(err, context.DeadlineExceeded) || result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != "failed" {
		t.Fatal("idle expiry lost bounded failed-cycle settlement", err)
	}
	var failures int
	var message string
	var noSuccess, future, originalActive bool
	if err := f.pg.QueryRow(context.Background(), `SELECT consecutive_failures,last_error,last_success_at IS NULL,next_check_at>now(),(SELECT is_active FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid`, f.board, f.original).Scan(&failures, &message, &noSuccess, &future, &originalActive); err != nil || failures != 1 || message != "native_task_timeout_failed" || !noSuccess || !future || !originalActive {
		t.Fatal("idle timeout changed failure, schedule or partial-inventory policy")
	}
	assertRichDeadlineAndLease(t, f, "lever")
}
