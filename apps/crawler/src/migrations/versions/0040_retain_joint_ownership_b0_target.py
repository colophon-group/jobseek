"""Retain immutable B0 targets for live joint owner admission.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def _journal_function() -> str:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_publication.sql"
    body = source.read_text(encoding="utf-8")
    return body[body.index("CREATE OR REPLACE FUNCTION") :]


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_b0_target.sql"
    op.execute(source.read_text(encoding="utf-8"))
    op.execute(
        _journal_function().replace(
            "    RETURN NEW;",
            "    IF NEW.phase IN ('publishing','published','active') AND NOT EXISTS ("
            "SELECT 1 FROM public.crawler_ownership_b0_target "
            "WHERE target_sha256=NEW.payload::jsonb->>'target_b0_manifest_sha256') "
            "THEN RAISE EXCEPTION 'crawler_ownership_target_required'; END IF;\n"
            "    RETURN NEW;",
        )
    )


def downgrade() -> None:
    op.execute("LOCK TABLE public.crawler_ownership_transition IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE public.crawler_ownership_b0_target IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.crawler_ownership_transition) "
        "OR EXISTS (SELECT 1 FROM public.crawler_ownership_b0_target) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute(_journal_function())
    op.execute("DROP INDEX public.crawler_ownership_transition_one_reserved_plan")
    op.execute("DROP TABLE public.crawler_ownership_b0_target")
    op.execute("DROP FUNCTION public.jobseek_crawler_ownership_b0_target_retained()")
