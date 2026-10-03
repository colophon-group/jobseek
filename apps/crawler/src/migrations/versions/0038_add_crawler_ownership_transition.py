"""Journal the coordinated ordinary/B0 cold ownership transition.

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
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_transition.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Retained journals must never disappear through an automated rollback.
    op.execute("LOCK TABLE public.crawler_ownership_transition IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.crawler_ownership_transition) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute("DROP TABLE public.crawler_ownership_transition")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_journal_transition()")
