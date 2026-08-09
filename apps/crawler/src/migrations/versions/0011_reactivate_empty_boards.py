"""Reactivate boards retired only because they returned no jobs.

An empty, successful board response is not evidence that the board endpoint
is permanently gone.  The old runtime state machine nevertheless changed a
previously non-empty board to ``gone`` and disabled it after six consecutive
empty checks.  Because disabled boards are intentionally excluded from both
Postgres and Redis scheduling, they could never observe a future opening and
self-recover.

The companion runtime change keeps confirmed-empty boards enabled as
``suspect`` while still delisting their stale postings.  This migration repairs
only the unambiguous legacy fingerprint: a ``gone``/disabled board with no
recorded failure or error and at least six successful empty checks.  Explicit
upstream-gone signals and failure-budget disables are deliberately excluded.

Deploys run migrations while workers are stopped, then run ``crawler sync``.
Sync re-disables any repaired row that is no longer present in ``boards.csv``
and schedules every still-configured row in Redis.

Revision ID: 0011
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


_REACTIVATE_EMPTY_BOARDS = """
UPDATE job_board
SET is_enabled = true,
    board_status = 'suspect',
    gone_at = NULL,
    next_check_at = now(),
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'gone'
  AND is_enabled = false
  AND consecutive_failures = 0
  AND last_error IS NULL
  AND last_non_empty_at IS NOT NULL
  AND empty_check_count >= 6
"""


def upgrade() -> None:
    op.execute(_REACTIVATE_EMPTY_BOARDS)


def downgrade() -> None:
    # Re-retiring these boards would recreate the production correctness bug,
    # and the migration does not retain enough history to distinguish rows
    # that have since recovered.  Runtime rollbacks remain compatible with a
    # suspect/enabled row, so data rollback is intentionally a no-op.
    pass
