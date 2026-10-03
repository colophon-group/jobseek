"""Retain complete approved native forward manifests before B0 transfer.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_b0_forward.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("LOCK TABLE public.crawler_ownership_b0_forward IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.crawler_ownership_b0_forward) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute("DROP TABLE public.crawler_ownership_b0_forward")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_b0_forward_retention()")
