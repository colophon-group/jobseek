"""Partial index for seniority count refresh.

``refresh_typesense_counts`` groups active, content-bearing postings by
``seniority_id``. On production data that aggregate was forced through the
broad ``idx_jp_active`` index and could hit the 30s local statement timeout.
This index matches the refresh predicate so Postgres can count only postings
that can contribute to seniority facet counts.

Created CONCURRENTLY so it does not lock ``job_posting`` on live boxes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_ACTIVE_CONTENT_SENIORITY_PREDICATE = (
    "is_active "
    "AND seniority_id IS NOT NULL "
    "AND description_r2_hash IS NOT NULL "
    "AND cardinality(titles) > 0 "
    "AND length(trim(titles[1])) > 0"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_jp_seniority_active_content ON job_posting (seniority_id) "
            f"WHERE {_ACTIVE_CONTENT_SENIORITY_PREDICATE}"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_jp_seniority_active_content")
