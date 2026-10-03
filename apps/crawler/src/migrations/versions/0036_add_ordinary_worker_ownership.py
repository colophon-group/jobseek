"""Persist immutable ordinary worker ownership plans.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "ordinary_worker_ownership.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE public.ordinary_worker_ownership_plan")
    op.execute("DROP FUNCTION public.jobseek_ordinary_ownership_transition()")
