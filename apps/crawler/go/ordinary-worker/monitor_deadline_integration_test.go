package worker

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealMonitorTaskDeadlinePersistsFailureAndReschedules(t *testing.T) {
	for _, tc := range []struct{ provider, metadata string }{
		{"lever", `{"token":"fixture","scraper_type":"skip"}`},
		{"oracle_hcm", `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","scraper_type":"oracle_hcm"}`},
		{"dom", `{"link_selector":"a.job","scraper_type":"skip"}`},
	} {
		t.Run(tc.provider, func(t *testing.T) {
			f := privateRichPipelineFixture(t, tc.provider, tc.metadata)
			claim, circuits := claimFixture(t, f)
			var entered atomic.Bool
			client := richPipelineHTTP(t, func(_ http.ResponseWriter, r *http.Request) {
				entered.Store(true)
				<-r.Context().Done()
			})
			c := fixtureLoopConfig()
			c.taskTimeout = 300 * time.Millisecond
			c.cancellationGrace = time.Second
			c.heartbeat = time.Hour
			started := time.Now()
			result, err := runLeasedClaim(context.Background(), c, runtimeServices{
				heartbeat: f.a.Heartbeat,
				execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
					return RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
				},
			}, claim, newRuntimeMetrics(1, c.stallTimeout))
			if !entered.Load() || !errors.Is(err, context.DeadlineExceeded) {
				t.Fatal("fixture did not reach the owned processing deadline")
			}
			if result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != "failed" {
				t.Fatal("controlled task deadline retained an unaccounted monitor lease")
			}
			ctx := context.Background()
			var failures int
			var message string
			var noSuccess bool
			var due time.Time
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,last_error,last_success_at IS NULL,next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &message, &noSuccess, &due); err != nil {
				t.Fatal("deadline readback failed")
			}
			if failures != 1 || message != "native_task_timeout_failed" || !noSuccess || !due.After(started) {
				t.Fatal("deadline did not record exactly one failed cycle and future schedule")
			}
			var active bool
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil || !active {
				t.Fatal("incomplete deadline inventory removed a live posting")
			}
			assertRichDeadlineAndLease(t, f, tc.provider)
		})
	}
}

func TestRealMonitorTaskDeadlineRejectsLostAuthorityAndReservation(t *testing.T) {
	for _, mode := range []string{"parent_canceled", "token_replaced", "publisher_reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "lever", `{"token":"fixture","scraper_type":"skip"}`)
			claim, circuits := claimFixture(t, f)
			parent, stop := context.WithCancel(context.Background())
			defer stop()
			var entered atomic.Bool
			client := richPipelineHTTP(t, func(_ http.ResponseWriter, r *http.Request) {
				ctx := context.Background()
				var err error
				switch mode {
				case "parent_canceled":
					stop()
				case "token_replaced":
					err = f.r.HSet(ctx, "inflight_tokens:simple", "monitor|lever|"+f.board, strings.Repeat("f", 32)).Err()
				case "publisher_reserved":
					_, err = f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board)
				}
				if err != nil {
					t.Error("private authority/publisher transition failed")
				}
				entered.Store(true)
				<-r.Context().Done()
			})
			c := fixtureLoopConfig()
			c.taskTimeout, c.cancellationGrace, c.heartbeat = 300*time.Millisecond, time.Second, time.Hour
			result, err := runLeasedClaim(parent, c, runtimeServices{heartbeat: f.a.Heartbeat,
				execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
					return RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
				}}, claim, newRuntimeMetrics(1, c.stallTimeout))
			if !entered.Load() || err == nil || result == nil || result.Settled || result.Cycle != nil {
				t.Fatal("deadline cleanup accepted canceled, stale or reserved work")
			}
			var failures int
			var state string
			if err := f.pg.QueryRow(context.Background(), "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil || failures != 0 {
				t.Fatal("rejected deadline cleanup changed failure accounting")
			}
			if err := f.pg.QueryRow(context.Background(), "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state); err != nil || state != "active" {
				t.Fatal("rejected deadline cleanup fabricated a completed fence")
			}
		})
	}
}
