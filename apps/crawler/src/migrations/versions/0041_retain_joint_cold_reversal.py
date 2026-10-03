"""Retain exact joint reversal intent before fresh epoch retirement.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def _previous_journal_function() -> str:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_publication.sql"
    body = source.read_text(encoding="utf-8")
    return body[body.index("CREATE OR REPLACE FUNCTION") :].replace(
        "    RETURN NEW;",
        "    IF NEW.phase IN ('publishing','published','active') AND NOT EXISTS ("
        "SELECT 1 FROM public.crawler_ownership_b0_target "
        "WHERE target_sha256=NEW.payload::jsonb->>'target_b0_manifest_sha256') "
        "THEN RAISE EXCEPTION 'crawler_ownership_target_required'; END IF;\n"
        "    RETURN NEW;",
    )


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_reversal.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        "LOCK TABLE public.crawler_ownership_transition,public.crawler_ownership_reversal "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.crawler_ownership_transition) "
        "OR EXISTS (SELECT 1 FROM public.crawler_ownership_reversal) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute(_previous_journal_function())
    op.execute("DROP TABLE public.crawler_ownership_reversal")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_reversal_transition()")
