"""Bound cold ordinary admission's legacy posting lease scan.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "ordinary_cold_lease_index.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_job_posting_cold_lease")
