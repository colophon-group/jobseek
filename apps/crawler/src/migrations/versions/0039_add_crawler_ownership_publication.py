"""Order joint Redis publication before ordinary database activation.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_publication.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("LOCK TABLE public.crawler_ownership_transition IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.crawler_ownership_transition) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    op.execute(
        "ALTER TABLE public.crawler_ownership_transition "
        "DROP CONSTRAINT crawler_ownership_transition_phase_check"
    )
    op.execute(
        "ALTER TABLE public.crawler_ownership_transition ADD CONSTRAINT "
        "crawler_ownership_transition_phase_check CHECK "
        "(phase IN ('pending','reserved','published','active','reversing','reversed','superseded'))"
    )
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_transition.sql"
    original = source.read_text(encoding="utf-8")
    start = original.index("CREATE FUNCTION public.jobseek_crawler_ownership_journal_transition()")
    end = original.index("CREATE TRIGGER crawler_ownership_journal_transition")
    op.execute(original[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
