"""Retain prior Go B0 reactivation approval and completion at retirement R.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_b0_reactivation.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        "LOCK TABLE public.crawler_ownership_b0_reactivation_completion, "
        "public.crawler_ownership_b0_reactivation IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.crawler_ownership_b0_reactivation) "
        "OR EXISTS(SELECT 1 FROM public.crawler_ownership_b0_reactivation_completion) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute("DROP TABLE public.crawler_ownership_b0_reactivation_completion")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_b0_reactivation_completion()")
    op.execute("DROP TABLE public.crawler_ownership_b0_reactivation")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_b0_reactivation()")
