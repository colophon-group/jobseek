package worker

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestMonitorCapacityLeavesDetailDiscoveryAvailable(t *testing.T) {
	for _, c := range []struct{ slots, monitors, details int }{{2, 4, 4}, {15, 30, 45}} {
		t.Run(fmt.Sprint(c.slots), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			slots := make(chan struct{}, c.slots)
			entered := make(chan queue.Kind, c.monitors+c.details)
			release := make(chan struct{})
			var releaseOnce sync.Once
			defer releaseOnce.Do(func() { close(release) })
			var monitors, peak atomic.Int64
			done := make(chan error, c.monitors+c.details)
			kinds := make([]queue.Kind, c.monitors+c.details)
			for i := range kinds {
				kinds[i] = queue.Scrape
				if i < c.monitors {
					kinds[i] = queue.Monitor
				}
			}
			for _, kind := range kinds {
				go func(kind queue.Kind) {
					_, err := executeWithMonitorSlot(ctx, slots, kind, func() (*GreenhouseClaimResult, error) {
						if kind == queue.Monitor {
							n := monitors.Add(1)
							for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
							}
							defer monitors.Add(-1)
						}
						entered <- kind
						select {
						case <-release:
							return &GreenhouseClaimResult{}, nil
						case <-ctx.Done():
							return nil, ctx.Err()
						}
					})
					done <- err
				}(kind)
			}
			counts := map[queue.Kind]int{}
			for i := 0; i < c.slots+c.details; i++ {
				counts[awaitRuntime(t, entered)]++
			}
			if counts[queue.Monitor] != c.slots || counts[queue.Scrape] != c.details || peak.Load() != int64(c.slots) {
				t.Fatal("monitor cap consumed detail discovery capacity", counts, peak.Load())
			}
			select {
			case <-entered:
				t.Fatal("monitor executed beyond its separate cap")
			default:
			}
			releaseOnce.Do(func() { close(release) })
			for i := 0; i < c.monitors+c.details; i++ {
				if err := awaitRuntime(t, done); err != nil {
					t.Fatal(err)
				}
			}
			if peak.Load() != int64(c.slots) || len(slots) != 0 {
				t.Fatal("monitor capacity was leaked or exceeded")
			}
		})
	}
}
func TestMonitorCapacityCancellationDoesNotEnterExecution(t *testing.T) {
	slots := make(chan struct{}, 1)
	slots <- struct{}{}
	ctx, cancel := context.WithCancel(context.Background())
	var executions atomic.Int64
	done := make(chan error, 1)
	go func() {
		_, err := executeWithMonitorSlot(ctx, slots, queue.Monitor, func() (*GreenhouseClaimResult, error) {
			executions.Add(1)
			return &GreenhouseClaimResult{}, nil
		})
		done <- err
	}()
	cancel()
	if err := awaitRuntime(t, done); !errors.Is(err, context.Canceled) || executions.Load() != 0 || len(slots) != 1 {
		t.Fatal("cancelled waiter entered execution or released another task's slot", err)
	}
	<-slots
	for i := 0; i < 100; i++ {
		if _, err := executeWithMonitorSlot(ctx, slots, queue.Monitor, func() (*GreenhouseClaimResult, error) {
			executions.Add(1)
			return nil, nil
		}); !errors.Is(err, context.Canceled) {
			t.Fatal("available slot bypassed cancellation", err)
		}
	}
	if executions.Load() != 0 || len(slots) != 0 {
		t.Fatal("cancellation raced capacity into origin execution")
	}
}

func TestRealMonitorCapacityWaitKeepsLeaseAndSettlesExactSchedule(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	claim, circuits := claimFixture(t, f)
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("publisher-reserved monitor made an origin request")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	if claim.Descriptor().Kind != queue.Monitor {
		t.Fatal("fixture did not acquire a real monitor claim")
	}
	slots := make(chan struct{}, 1)
	slots <- struct{}{}
	beats := make(chan struct{}, 10)
	var executions atomic.Int64
	c := fixtureLoopConfig()
	c.heartbeat = 10 * time.Millisecond
	c.taskTimeout = 2 * time.Second
	m := newRuntimeMetrics(1, c.stallTimeout)
	services := runtimeServices{
		monitorSlots: slots,
		heartbeat: func(ctx context.Context, claim *queue.Claim) error {
			err := f.a.Heartbeat(ctx, claim)
			if err == nil {
				beats <- struct{}{}
			}
			return err
		},
		execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
			executions.Add(1)
			return RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
		},
	}
	type outcome struct {
		result *GreenhouseClaimResult
		err    error
	}
	done := make(chan outcome, 1)
	go func() {
		result, err := runLeasedClaim(ctx, c, services, claim, m)
		done <- outcome{result, err}
	}()
	awaitRuntime(t, beats)
	awaitRuntime(t, beats)
	if executions.Load() != 0 || f.r.ZCard(ctx, "inflight:simple").Val() != 1 {
		t.Fatal("waiting monitor fetched or lost its live lease")
	}
	<-slots
	result := awaitRuntime(t, done)
	if result.err != nil || executions.Load() != 1 || len(slots) != 0 {
		t.Fatal("released monitor did not execute and return capacity", result.err)
	}
	assertClaimReceiptSettlement(t, f, result.result)
}

func TestRealMonitorCapacityWaitCancellationRetainsUnsettledClaim(t *testing.T) {
	for _, loseToken := range []bool{false, true} {
		name := "cancel"
		if loseToken {
			name = "lost-token"
		}
		t.Run(name, func(t *testing.T) {
			f := privatePipelineFixture(t)
			claim, _ := claimFixture(t, f)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var dueBefore time.Time
			if err := f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&dueBefore); err != nil {
				t.Fatal(err)
			}
			slots := make(chan struct{}, 1)
			slots <- struct{}{}
			beats := make(chan struct{}, 10)
			var executions atomic.Int64
			c := fixtureLoopConfig()
			c.heartbeat = 10 * time.Millisecond
			c.taskTimeout = 2 * time.Second
			m := newRuntimeMetrics(1, c.stallTimeout)
			services := runtimeServices{
				monitorSlots: slots,
				heartbeat: func(ctx context.Context, claim *queue.Claim) error {
					err := f.a.Heartbeat(ctx, claim)
					if err == nil {
						beats <- struct{}{}
					}
					return err
				},
				execute: func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error) {
					executions.Add(1)
					return &GreenhouseClaimResult{Settled: true}, nil
				},
			}
			done := make(chan error, 1)
			go func() {
				result, err := runLeasedClaim(ctx, c, services, claim, m)
				if result != nil {
					t.Error("unexecuted monitor fabricated a result")
				}
				done <- err
			}()
			awaitRuntime(t, beats)
			if loseToken {
				if err := f.r.HSet(ctx, "inflight_tokens:simple", "monitor|greenhouse|"+f.board, strings.Repeat("f", 32)).Err(); err != nil {
					t.Fatal(err)
				}
			} else {
				cancel()
			}
			err := awaitRuntime(t, done)
			expected := error(context.Canceled)
			if loseToken {
				expected = queue.ErrAuthorityLost
			}
			if !errors.Is(err, expected) || executions.Load() != 0 || len(slots) != 1 {
				t.Fatal("cancelled monitor wait entered execution or lost another slot", err)
			}
			read := context.Background()
			var state string
			var failures int
			var dueAfter time.Time
			if err := f.pg.QueryRow(read, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state); err != nil || state != "active" {
				t.Fatal("unexecuted monitor fabricated a terminal fence", err, state)
			}
			if err := f.pg.QueryRow(read, "SELECT consecutive_failures,next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &dueAfter); err != nil || failures != 0 || !dueAfter.Equal(dueBefore) {
				t.Fatal("cancelled capacity wait changed canonical scheduling", err)
			}
			if f.r.ZCard(read, "inflight:simple").Val() != 1 {
				t.Fatal("unsettled capacity wait dropped the recovery lease")
			}
		})
	}
}
