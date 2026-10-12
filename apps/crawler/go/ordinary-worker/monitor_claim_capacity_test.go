package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"sync/atomic"
	"testing"
	"time"
)

// Obtain opaque descriptors from isolated actual queue fixtures. This fake loop
// tests scheduling only; it neither writes nor settles these fixture rows.
func TestRuntimeMonitorSaturationKeepsDetailSlotsAvailable(t *testing.T) {
	f := privateRichPipelineFixtureURL(t, "greenhouse", `{"token":"fixture","scraper_type":"skip","_monitor_config_fingerprint":"fixture"}`, "https://boards.greenhouse.io/"+fixtureID(t))
	monitor, err := f.a.Claim(context.Background(), queue.Simple)
	if err != nil || monitor == nil || monitor.Descriptor().Kind != queue.Monitor {
		t.Fatal("monitor descriptor unavailable", err)
	}
	// The local fixture database permits one active plan. Retire its descriptor
	// source before constructing the independent detail descriptor fixture.
	if _, err := f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active' AND payload::jsonb->'members' @> jsonb_build_array(jsonb_build_object('board_id',$1::text))", f.board); err != nil {
		t.Fatal("private monitor descriptor source retirement failed", err)
	}
	_, _, detail := jsonldOwnedFixture(t)
	if detail.Descriptor().Kind != queue.Scrape {
		t.Fatal("detail descriptor unavailable")
	}
	c := fixtureLoopConfig()
	c.concurrency = 4
	c.monitorConcurrency = 2
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	entered := make(chan queue.Kind, 8)
	var monitors atomic.Int64
	services := runtimeServices{
		claim:       func(context.Context) (*queue.Claim, error) { return monitor, nil },
		claimDetail: func(context.Context) (*queue.Claim, error) { return detail, nil },
		heartbeat:   func(context.Context, *queue.Claim) error { return nil },
		execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
			if claim.Descriptor().Kind == queue.Monitor {
				if monitors.Add(1) > 2 {
					t.Error("monitor cap exceeded")
				}
				defer monitors.Add(-1)
			}
			entered <- claim.Descriptor().Kind
			<-ctx.Done()
			return &GreenhouseClaimResult{Settled: true}, nil
		},
	}
	metrics := newRuntimeMetrics(c.concurrency, c.stallTimeout)
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, services, metrics) }()
	kinds := map[queue.Kind]int{}
	timer := time.NewTimer(250 * time.Millisecond)
	defer timer.Stop()
	for count := 0; count < 4; count++ {
		select {
		case kind := <-entered:
			kinds[kind]++
		case <-timer.C:
			stop()
			<-done
			t.Fatalf("shared slots held by waiting monitors; entered=%v", kinds)
		}
	}
	if kinds[queue.Monitor] != 2 || kinds[queue.Scrape] != 2 {
		stop()
		<-done
		t.Fatalf("detail capacity lost under monitor saturation: %v", kinds)
	}
	stop()
	if err := awaitRuntime(t, done); err != nil {
		t.Fatal(err)
	}
}
