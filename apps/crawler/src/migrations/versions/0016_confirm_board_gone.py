"""Replace one-shot board retirement with recoverable gone confirmation.

Legacy ``gone`` rows contain only one provider-gone observation. They become
schedulable pending confirmations and are deterministically spread over the
first fifteen minutes after deploy. The following CSV sync disables historical
rows that are no longer configured and enqueues configured rows at their
durable due time.

Revision ID: 0016
Create Date: 2026-08-03
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_ADD_GONE_CONFIRMATION_STATE = """
ALTER TABLE job_board
    ADD COLUMN IF NOT EXISTS gone_confirmation_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gone_first_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gone_last_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_gone_error TEXT,
    ADD COLUMN IF NOT EXISTS last_gone_endpoint TEXT,
    ADD COLUMN IF NOT EXISTS last_gone_status SMALLINT,
    ADD COLUMN IF NOT EXISTS gone_transition_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gone_recovery_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jb_gone_due
    ON job_board (next_check_at, id)
    WHERE is_enabled = true AND board_status IN ('gone_pending', 'gone');
"""


_REACTIVATE_LEGACY_GONE_BOARDS = """
UPDATE job_board
SET is_enabled = true,
    board_status = 'gone_pending',
    gone_confirmation_count = GREATEST(gone_confirmation_count, 1),
    gone_first_confirmed_at = COALESCE(gone_first_confirmed_at, gone_at, updated_at, now()),
    gone_last_confirmed_at = COALESCE(gone_last_confirmed_at, gone_at, updated_at, now()),
    last_gone_error = COALESCE(
        last_gone_error,
        last_error,
        'Legacy one-shot BoardGoneError; endpoint and status unavailable'
    ),
    gone_transition_count = GREATEST(gone_transition_count, 1),
    next_check_at = now() + (
        abs(hashtextextended(id::text, 6156) % 900)::text || ' seconds'
    )::interval,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'gone';
"""


def upgrade() -> None:
    op.execute(_ADD_GONE_CONFIRMATION_STATE)
    op.execute(_REACTIVATE_LEGACY_GONE_BOARDS)


def downgrade() -> None:
    op.execute("""
        UPDATE job_posting jp
        SET is_active = false,
            next_scrape_at = NULL,
            updated_at = now()
        FROM job_board jb
        WHERE jp.board_id = jb.id
          AND jp.is_active = true
          AND jb.board_status = 'gone_pending';

        UPDATE job_board
        SET is_enabled = false,
            board_status = 'gone',
            gone_at = COALESCE(gone_at, gone_last_confirmed_at, now()),
            lease_owner = NULL,
            leased_until = NULL,
            updated_at = now()
        WHERE board_status IN ('gone_pending', 'gone');

        DROP INDEX IF EXISTS idx_jb_gone_due;
        ALTER TABLE job_board
            DROP COLUMN IF EXISTS gone_recovery_count,
            DROP COLUMN IF EXISTS gone_transition_count,
            DROP COLUMN IF EXISTS last_gone_status,
            DROP COLUMN IF EXISTS last_gone_endpoint,
            DROP COLUMN IF EXISTS last_gone_error,
            DROP COLUMN IF EXISTS gone_last_confirmed_at,
            DROP COLUMN IF EXISTS gone_first_confirmed_at,
            DROP COLUMN IF EXISTS gone_confirmation_count;
    """)
