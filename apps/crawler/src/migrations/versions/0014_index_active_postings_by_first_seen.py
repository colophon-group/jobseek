"""Index the bounded active-posting window used by daily annotation sampling.

The sampler filters active postings by ``first_seen_at`` and consumes them in
newest-first order. At production scale, the previous indexes forced
PostgreSQL to scan a broad active set and sort the one-day result, which
exceeded the crawler pool's 30-second statement timeout.

The UUID tie-breaker makes equal-timestamp rows deterministic and matches the
sampler's complete order. Created CONCURRENTLY so the live posting writers are
not blocked while the index is built.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_jp_active_first_seen"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A canceled CREATE INDEX CONCURRENTLY leaves an invalid same-name
        # relation behind. Remove either that artifact or an operator-created
        # collision so a retry always installs this revision's exact shape.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            "CREATE INDEX CONCURRENTLY "
            f"{INDEX_NAME} ON job_posting (first_seen_at DESC, id) "
            "WHERE is_active"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
