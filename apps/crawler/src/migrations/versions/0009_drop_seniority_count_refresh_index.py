"""Drop the obsolete seniority count refresh index.

``refresh_typesense_counts`` now reads seniority counts from the Typesense
``seniority_id`` facet, matching the other taxonomy count refreshes. The
partial Postgres index added in revision 0008 was only used by the removed
aggregate and still could not keep that query below the 30-second production
statement timeout.

Dropped concurrently so the migration does not block writes to
``job_posting`` on live boxes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
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
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_jp_seniority_active_content")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_jp_seniority_active_content ON job_posting (seniority_id) "
            f"WHERE {_ACTIVE_CONTENT_SENIORITY_PREDICATE}"
        )
