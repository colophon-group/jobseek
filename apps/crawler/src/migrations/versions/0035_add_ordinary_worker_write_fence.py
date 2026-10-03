"""Retain native ordinary worker claim/write authority.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "ordinary_worker_write_fence.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE public.ordinary_worker_write_fence")
    op.execute("DROP FUNCTION public.jobseek_ordinary_worker_enforce_epoch()")
