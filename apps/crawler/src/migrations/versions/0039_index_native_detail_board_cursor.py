"""Bound native detail traversal by canonical board and posting ID.

Production read-only plans for the JSON-LD cohort scanned and sorted complete
board histories to return 64 postings, taking up to 10.7 seconds. The native
worker also traverses inactive/future receipts for interrupted ACK recovery,
so this index must include every posting rather than only active/due rows.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_jp_board_id_cursor"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A canceled concurrent build leaves an invalid same-name index. A
        # deployment retry must install the intended complete index shape.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX_NAME}")
        op.execute(f"CREATE INDEX CONCURRENTLY {INDEX_NAME} ON public.job_posting (board_id, id)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX_NAME}")
