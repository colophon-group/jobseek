package worker

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func progressConfig(t *testing.T, explicit string) RuntimeConfig {
	t.Helper()
	env := fixtureRuntimeEnvironment()
	env["ORDINARY_GO_TASK_TIMEOUT_SECONDS"] = explicit
	c, err := ReadRuntimeConfig(func(key string) string { return env[key] }, strings.Repeat("a", 40))
	if err != nil {
		t.Fatal(err)
	}
	c.taskTimeout = 100 * time.Millisecond
	c.cancellationGrace = 200 * time.Millisecond
	return c
}

func TestMonitorCommittedProgressRenewsExistingInactivityBudgetAcrossProfiles(t *testing.T) {
	for _, provider := range []string{"amazon", "beisen", "api_sniffer"} {
		t.Run(provider, func(t *testing.T) {
			c := progressConfig(t, "")
			ctx, cancel, guard := newClaimProcessingContext(context.Background(), c, queue.Task{Kind: queue.Monitor, Worker: queue.Simple, Config: map[string]string{"crawler_type": provider}})
			defer cancel()
			if _, fixed := ctx.Deadline(); fixed || guard.progressTimeout == nil {
				t.Fatal("streaming monitor retained a fixed wall-clock deadline")
			}
			pulses := 0
			guard.onProgress = func() { pulses++ }
			ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
			child, cancelChild := context.WithCancel(ctx)
			defer cancelChild()
			started := time.Now()
			for i := 0; i < 5; i++ {
				time.Sleep(35 * time.Millisecond)
				if _, err := WriteGreenhouseInventory(ctx, &pipelineSink{}, &pipelinePreparer{}, pipelineInventory(1)); err != nil {
					t.Fatal("healthy committed monitor progress timed out", err)
				}
			}
			if time.Since(started) <= c.taskTimeout || pulses != 5 || ctx.Err() != nil {
				t.Fatal("committed batches failed to renew the original inactivity budget")
			}
			select {
			case <-ctx.Done():
			case <-time.After(time.Second):
				t.Fatal("stalled monitor escaped its original inactivity budget")
			}
			if !errors.Is(ctx.Err(), context.DeadlineExceeded) || pulseMonitorProgress(ctx) == nil || guard.progressTimeout.pulse() {
				t.Fatal("expired monitor was revived by late progress")
			}
			<-child.Done()
			if child.Err() != context.DeadlineExceeded || context.Cause(ctx) != context.DeadlineExceeded {
				t.Fatal("monitor inactivity deadline lost its identity in child contexts")
			}
			cleanup, stop, ok := monitorFailureContext(ctx)
			if !ok || cleanup.Err() != nil {
				t.Fatal("owned inactivity expiration lost bounded failure settlement")
			}
			defer stop()
			deadline, _ := cleanup.Deadline()
			expired, _ := guard.progressTimeout.expiredDeadline()
			if deadline.After(expired.Add(c.cancellationGrace)) {
				t.Fatal("failure settlement exceeded the existing cancellation grace")
			}
		})
	}
}

func TestExplicitMonitorCapsDetailsAndNjoynRemainAbsolute(t *testing.T) {
	for _, mode := range []string{"operator_cap", "detail", "njoyn"} {
		t.Run(mode, func(t *testing.T) {
			c := progressConfig(t, "")
			task := queue.Task{Kind: queue.Monitor, Worker: queue.Simple}
			if mode == "operator_cap" {
				c = progressConfig(t, "1")
			} else if mode == "detail" {
				task.Kind = queue.Scrape
			} else {
				task.Worker = queue.Browser
				task.Config = map[string]string{"crawler_type": "njoyn"}
				c.njoynTaskTimeout = c.taskTimeout
			}
			ctx, cancel, guard := newClaimProcessingContext(context.Background(), c, task)
			defer cancel()
			if _, fixed := ctx.Deadline(); !fixed || guard.progressTimeout != nil {
				t.Fatal("operator/provider/detail deadline became renewable")
			}
			ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
			tick := time.NewTicker(10 * time.Millisecond)
			defer tick.Stop()
			for ctx.Err() == nil {
				select {
				case <-ctx.Done():
				case <-tick.C:
					_ = pulseMonitorProgress(ctx)
				}
			}
			if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
				t.Fatal("fixed deadline changed failure identity")
			}
		})
	}
}

func TestMonitorProgressParentCancellationNeverGrantsFailureAuthority(t *testing.T) {
	parent, cancelParent := context.WithCancel(context.Background())
	c := progressConfig(t, "")
	ctx, cancel, guard := newClaimProcessingContext(parent, c, queue.Task{Kind: queue.Monitor})
	defer cancel()
	ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
	cancelParent()
	<-ctx.Done()
	if ctx.Err() != context.Canceled || pulseMonitorProgress(ctx) == nil {
		t.Fatal("progress escaped parent cancellation")
	}
	if _, _, ok := monitorFailureContext(ctx); ok {
		t.Fatal("parent cancellation created fresh write authority")
	}
}

func TestFailedMonitorWritesDoNotPulseAndCommittedPrefixDoes(t *testing.T) {
	for _, firstFailure := range []bool{true, false} {
		c := progressConfig(t, "")
		ctx, cancel, guard := newClaimProcessingContext(context.Background(), c, queue.Task{Kind: queue.Monitor})
		pulses := 0
		guard.onProgress = func() { pulses++ }
		ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
		failAt, want := 2, 1
		if firstFailure {
			failAt, want = 1, 0
		}
		sink := &pipelineSink{failAt: failAt, cause: errors.New("fixture write failure")}
		_, err := WriteGreenhouseInventory(ctx, sink, &pipelinePreparer{}, pipelineInventory(1001))
		if err == nil || !sink.invalidated || pulses != want {
			t.Fatal("failed writes renewed progress or committed prefix was lost")
		}
		cancel()
	}
}

func TestMonitorProgressConcurrentPulsesAndStopAreRaceSafe(t *testing.T) {
	ctx := newMonitorProgressContext(context.Background(), time.Second)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				_ = ctx.pulse()
				_ = ctx.Err()
			}
		}()
	}
	ctx.stop()
	wg.Wait()
	if ctx.Err() != context.Canceled || ctx.pulse() {
		t.Fatal("stopped monitor progress context remained live")
	}
}
