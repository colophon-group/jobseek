"""Partial index on job_posting(company_id) WHERE is_active.

The watchlist count aggregation (see ``sync_watchlists_typesense``) moved
from Supabase to local Postgres to cut Supabase compute spend; the query
shape is ``WHERE is_active AND company_id = ANY($1)``. Existing indexes
are ``idx_jp_company(company_id)`` (full) and ``idx_jp_active WHERE
is_active`` (flag only). Neither lets the planner drive a nested-loop
that touches only active rows for a small set of companies. This partial
index does.

Created CONCURRENTLY so it does not lock ``job_posting`` on live boxes.

Revision ID: 0005
Create Date: 2026-04-24
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_jp_company_active ON job_posting (company_id) "
            "WHERE is_active"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_jp_company_active")
