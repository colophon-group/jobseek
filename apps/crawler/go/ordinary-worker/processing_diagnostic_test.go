package worker

import (
	"context"
	"errors"
	"fmt"
	"strings"
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
