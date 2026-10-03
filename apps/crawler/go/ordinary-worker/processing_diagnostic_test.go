package worker

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
)

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
