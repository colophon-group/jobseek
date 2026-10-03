"""Retain fresh ordinary rollback preparation at the reserved retirement epoch.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_ordinary_restoration.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("LOCK TABLE public.crawler_ownership_ordinary_restoration IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.crawler_ownership_ordinary_restoration) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute("DROP TABLE public.crawler_ownership_ordinary_restoration")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_ordinary_restoration()")
