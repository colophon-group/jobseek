"""Retain approved native B0 restoration manifests before Redis effects.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_b0_restoration.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("LOCK TABLE public.crawler_ownership_b0_restoration IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.crawler_ownership_b0_restoration) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute("DROP TABLE public.crawler_ownership_b0_restoration")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_b0_restoration_transition()")
