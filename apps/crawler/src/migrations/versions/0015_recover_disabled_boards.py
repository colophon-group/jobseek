"""Replace terminal failure-budget disables with recoverable quarantine.

Ordinary monitor failures used to make a configured board permanently
unschedulable after five strikes.  The deploy sequence runs this migration
while workers are stopped and then runs ``crawler sync`` before workers start:

* every legacy failure-budget ``disabled`` row is made schedulable as
  ``quarantined``;
* exact Ashby boards are due immediately (the deterministic first recovery
  cohort from #6157);
* the remaining rows are deterministically spread across six hours to avoid a
  retry storm; and
* the following CSV sync re-disables rows that are no longer configured and
  seeds only configured rows into Redis.

The additional columns retain the latest quarantine/recovery evidence and a
monotonic recovery count without copying unbounded error history into metrics.

Revision ID: 0015
Create Date: 2026-08-03
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


_ADD_QUARANTINE_STATE = """
ALTER TABLE job_board
    ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_quarantined_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_quarantine_error TEXT,
    ADD COLUMN IF NOT EXISTS quarantine_probe_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_recovered_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recovery_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jb_quarantine_due
    ON job_board (next_check_at, id)
    WHERE is_enabled = true AND board_status = 'quarantined';
"""


_REACTIVATE_DISABLED_BOARDS = """
UPDATE job_board
SET is_enabled = true,
    board_status = 'quarantined',
    quarantined_at = COALESCE(quarantined_at, updated_at, now()),
    last_quarantined_at = COALESCE(last_quarantined_at, updated_at, now()),
    last_quarantine_error = COALESCE(last_quarantine_error, last_error),
    quarantine_probe_count = GREATEST(quarantine_probe_count, 1),
    next_check_at = now() + CASE
        WHEN crawler_type = 'ashby' THEN interval '0 seconds'
        ELSE (
            abs(hashtextextended(id::text, 6157) % 21600)::text || ' seconds'
        )::interval
    END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'disabled'
  AND is_enabled = false;
"""


def upgrade() -> None:
    op.execute(_ADD_QUARANTINE_STATE)
    op.execute(_REACTIVATE_DISABLED_BOARDS)


def downgrade() -> None:
    # A downgraded runtime does not understand the quarantine status. Restore
    # its old terminal representation before removing the evidence columns.
    op.execute("""
        UPDATE job_board
        SET is_enabled = false,
            board_status = 'disabled',
            lease_owner = NULL,
            leased_until = NULL,
            updated_at = now()
        WHERE board_status = 'quarantined';

        DROP INDEX IF EXISTS idx_jb_quarantine_due;
        ALTER TABLE job_board
            DROP COLUMN IF EXISTS recovery_count,
            DROP COLUMN IF EXISTS last_recovered_at,
            DROP COLUMN IF EXISTS quarantine_probe_count,
            DROP COLUMN IF EXISTS last_quarantine_error,
            DROP COLUMN IF EXISTS last_quarantined_at,
            DROP COLUMN IF EXISTS quarantined_at;
    """)
