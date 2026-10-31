package worker

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestConfigurationDiagnosticsKeepCodeOwnedCausePhaseAndFatalSentinel(t *testing.T) {
	inner := claimRunError("detail_render_actions", queue.ErrConfiguration)
	err := claimRunError("detail_execution", fmt.Errorf("never-print-secret: %w", inner))
	err = claimRunError("task", err)
	var diagnostic *ClaimRunError
	if !errors.As(err, &diagnostic) || !errors.Is(err, queue.ErrConfiguration) || diagnostic.Kind != "configuration" || diagnostic.causePhase != "detail_render_actions" || !strings.Contains(err.Error(), "cause_phase=detail_render_actions") || strings.Contains(err.Error(), "never-print") {
		t.Fatal("nested configuration diagnostic lost safe phase or fatal identity")
	}
	// External code can construct the exported error type. Its public fields
	// must not become inherited context, even when it wraps a fatal sentinel.
	untrusted := &ClaimRunError{Phase: "never-print-secret", Kind: "never-print-sql", cause: queue.ErrConfiguration}
	err = claimRunError("detail_execution", untrusted)
	if !errors.Is(err, queue.ErrConfiguration) || strings.Contains(err.Error(), "never-print") || strings.Contains(err.Error(), "cause_phase=") {
		t.Fatal("public error fields became trusted cause context")
	}
}

func TestRealAuthorityDiagnosticKeepsFatalIdentityAndNoCacheValues(t *testing.T) {
	f, authority, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld"}`, "", queue.Simple)
	if err := f.r.HSet(context.Background(), "scrape:"+f.original, "private_field", "never-print-secret").Err(); err != nil {
		t.Fatal(err)
	}
	cause := authority.Heartbeat(context.Background(), claim)
	err := claimRunError("settlement", fmt.Errorf("never-print-secret: %w", cause))
	if !errors.Is(err, queue.ErrAuthorityLost) || !strings.Contains(err.Error(), "authority_reason=cache_snapshot") || strings.Contains(err.Error(), "never-print") || strings.Contains(err.Error(), f.original) {
		t.Fatal("authority diagnostic lost fatal identity or exposed a cache value")
	}
}

func TestRealCacheSnapshotRefusalDoesNotCancelUnrelatedWork(t *testing.T) {
	f, authority, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld"}`, "", queue.Simple)
	if err := f.r.HSet(context.Background(), "scrape:"+f.original, "private_field", "never-print-secret").Err(); err != nil {
		t.Fatal(err)
	}
	rejected := authority.Heartbeat(context.Background(), claim)
	if !errors.Is(rejected, queue.ErrAuthorityLost) || queue.AuthorityLossReason(rejected) != "cache_snapshot" {
		t.Fatal("fixture did not produce a real claim-local cache refusal")
	}
	if runtimeFatalError(rejected) || !runtimeFatalError(queue.ErrAuthorityLost) || !runtimeFatalError(errors.Join(rejected, ErrDrainTimeout)) {
		t.Fatal("cache refusal suppressed global authority or unsafe-drain failure")
	}
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	c := fixtureLoopConfig()
	m := newRuntimeMetrics(c.concurrency, c.stallTimeout)
	stale, healthy, followup := &queue.Claim{}, &queue.Claim{}, &queue.Claim{}
	healthyStarted, healthyRelease := make(chan struct{}), make(chan struct{})
	healthyDone, followupDone := make(chan struct{}), make(chan struct{})
	var claims atomic.Int32
	s := runtimeServices{
		claim: func(context.Context) (*queue.Claim, error) {
			switch claims.Add(1) {
			case 1:
				return stale, nil
			case 2:
				return healthy, nil
			case 3:
				return followup, nil
			default:
				return nil, nil
			}
		},
		heartbeat: func(context.Context, *queue.Claim) error { return nil },
		execute: func(ctx context.Context, claimed *queue.Claim) (*GreenhouseClaimResult, error) {
			switch claimed {
			case stale:
				<-healthyStarted
				return nil, claimRunError("settlement", rejected)
			case healthy:
				close(healthyStarted)
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case <-healthyRelease:
					close(healthyDone)
				}
			case followup:
				close(followupDone)
			}
			return &GreenhouseClaimResult{TaskKind: queue.Monitor, Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}}, nil
		},
	}
	done := make(chan error, 1)
	go func() { done <- runWorkerLoop(ctx, c, s, m) }()
	awaitRuntime(t, followupDone)
	close(healthyRelease)
	awaitRuntime(t, healthyDone)
	stop()
	if err := awaitRuntime(t, done); err != nil || m.tasks["authority_lost"] != 1 {
		t.Fatal("one stale snapshot cancelled unrelated work or lost failure accounting", err)
	}
}

func TestMonitorTimeoutDiagnosticKeepsTrustedPhaseAndDeadlineIdentity(t *testing.T) {
	private := fmt.Errorf("never-print-private: %w", queue.ErrConfiguration)
	for _, tc := range []struct {
		phase, want string
		cause       error
	}{
		{"fetch", "fetch", private},
		{"processing", "preparation", claimRunError("preparation", private)},
		{"processing", "posting_write", claimRunError("posting_write", private)},
		{"processing", "processing", &ClaimRunError{Phase: "never-print-private", Kind: "never-print-private", cause: private}},
	} {
		err := claimRunTimeout(tc.phase, tc.cause)
		var diagnostic *ClaimRunError
		if !errors.As(err, &diagnostic) || diagnostic.Phase != "task" || diagnostic.Kind != "deadline" || diagnostic.causePhase != tc.want || !errors.Is(err, context.DeadlineExceeded) || errors.Is(err, queue.ErrConfiguration) || strings.Contains(err.Error(), "never-print") {
			t.Fatalf("timeout lost trusted phase, changed failure identity or leaked context: %s", tc.want)
		}
	}
}

func TestProcessingFailureDiagnosticsRetainCauseWithoutDatabaseMessages(t *testing.T) {
	for _, tc := range []struct{ code, kind string }{
		{"57014", "query_cancelled"}, {"40P01", "deadlock"}, {"55P03", "lock_timeout"}, {"23514", "database"},
	} {
		cause := &pgconn.PgError{Code: tc.code, Message: "never-print-secret", Detail: "never-print-sql"}
		err := claimRunError("posting_write", fmt.Errorf("private context: %w", cause))
		var diagnostic *ClaimRunError
		if !errors.As(err, &diagnostic) || diagnostic.Phase != "posting_write" || diagnostic.Kind != tc.kind || !errors.Is(err, cause) || strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), "never-print") {
			t.Fatalf("unsafe or missing diagnostic for %s", tc.code)
		}
	}
}

func TestProcessingDiagnosticsDistinguishPreparationAndWriteFailures(t *testing.T) {
	for _, phase := range []string{"preparation", "posting_write"} {
		cause := errors.New("never-print-secret")
		preparer := &pipelinePreparer{}
		sink := &pipelineSink{}
		if phase == "preparation" {
			preparer.failAt, preparer.cause = 1, cause
		} else {
			sink.failAt, sink.cause = 1, cause
		}
		result, err := PersistGreenhouseInventory(context.Background(), sink, preparer, pipelineInventory(1))
		var diagnostic *ClaimRunError
		if !errors.As(err, &diagnostic) || diagnostic.Phase != phase || !errors.Is(err, cause) || sink.finished || !sink.invalidated || result.Cycle != nil {
			t.Fatalf("failure lost phase or finalized inventory: %s", phase)
		}
		if strings.Contains(diagnostic.Error(), "never-print") {
			t.Fatal("preparation diagnostic leaked cause")
		}
	}
}
