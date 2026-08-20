"""Cover the gone-guard active/missing count by board and last-seen time.

The blast-radius guard runs once per successful board monitor and counts both
active rows and the active rows not seen during the current cycle. The old
query could only use the broad ``job_posting(board_id)`` index, then filter
every posting belonging to a large board. Keep only active rows in this
partial index and order the second key by ``last_seen_at`` so PostgreSQL can
answer both counts from the same narrow index-only scan.

Created CONCURRENTLY so deploys do not lock ``job_posting`` while the index is
built on the live crawler database.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_jp_board_active_last_seen"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A canceled CREATE INDEX CONCURRENTLY leaves an invalid same-name
        # relation behind. Remove either that artifact or an operator-created
        # collision so a retry always installs this revision's exact shape.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            "CREATE INDEX CONCURRENTLY "
            f"{INDEX_NAME} ON job_posting (board_id, last_seen_at) "
            "WHERE is_active"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
