// Fenced ordinary-monitor terminal receipts for durable source coverage.
package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"

	"github.com/jackc/pgx/v5"
)

func sourceCoverageOutcome(inventory GreenhouseInventorySummary, processed, unique int, skipped string) string {
	if !inventory.Truncated && inventory.ProcessingFiltered == 0 &&
		inventory.Discovered == processed && processed == unique && skipped == "" {
		return "complete"
	}
	return "partial"
}

// recordSourceCoverage binds one monitoring observation to the same fenced
// transaction as its canonical terminal receipt. Partial and failed work is
// evidence of interruption, never evidence that the source was continuously
// checked. The migration starts all scopes at deployment time; old checks are
// deliberately not reconstructed from latest-state fields.
func (c *GreenhouseCycle) recordSourceCoverage(ctx context.Context, tx pgx.Tx, outcome string) error {
	if c == nil || c.claim == nil || c.claim.task.claimToken == "" || c.startedAt.IsZero() {
		return ErrConfiguration
	}
	attempt := sha256.Sum256([]byte(c.claim.task.claimToken))
	tag, err := tx.Exec(ctx, `INSERT INTO public.source_coverage_check
    (board_id, revision, attempt_hash, started_at, finished_at, next_due_at, outcome)
SELECT board.id, scope.revision, $2, $3::timestamptz, clock_timestamp(),
       board.next_check_at,
       CASE WHEN $4::text = 'complete'
              AND (NULLIF(board.metadata ->> '_monitor_config_fingerprint', '') IS NULL
                   OR board.board_status <> 'active' OR NOT board.is_enabled)
            THEN 'partial' ELSE $4::text END
FROM public.job_board AS board
JOIN public.source_coverage_scope AS scope ON scope.board_id = board.id
WHERE board.id = $1::uuid`, c.claim.task.ID, hex.EncodeToString(attempt[:]), c.startedAt, outcome)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return ErrConfiguration
	}
	// A checked board incrementally discards obsolete private facts. This bound
	// keeps pruning inside the existing short monitor transaction and leaves
	// published Justup editions responsible for their own historical evidence.
	_, err = tx.Exec(ctx, `DELETE FROM public.source_coverage_check
WHERE id IN (SELECT id FROM public.source_coverage_check
             WHERE board_id=$1::uuid AND finished_at<clock_timestamp()-interval '45 days'
             ORDER BY finished_at LIMIT 256)`, c.claim.task.ID)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `DELETE FROM public.source_coverage_posting_first
WHERE posting_id IN (SELECT posting_id FROM public.source_coverage_posting_first
                     WHERE board_id=$1::uuid AND first_observed_at<clock_timestamp()-interval '45 days'
                     ORDER BY first_observed_at LIMIT 1024)`, c.claim.task.ID)
	return err
}
