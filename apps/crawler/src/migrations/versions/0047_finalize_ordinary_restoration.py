"""Finalize ordinary restoration atomically at reserved R.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_restoration_finalization.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        "LOCK TABLE public.crawler_ownership_restoration_completion, "
        "public.crawler_ownership_restoration_publication, "
        "public.crawler_ownership_restoration_finalization IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_finalization) "
        "OR EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_publication) "
        "OR EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_completion) "
        "THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF; END $$"
    )
    for table, trigger in (
        ("crawler_ownership_reversal", "crawler_restoration_reversal_integrity"),
        ("crawler_ownership_transition", "crawler_restoration_journal_integrity"),
        ("ordinary_worker_ownership_plan", "crawler_restoration_owner_integrity"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON public.{table}")
    op.execute("DROP FUNCTION public.jobseek_crawler_restoration_integrity()")
    source = Path(__file__).parent.parent / "sql" / "crawler_ownership_reversal.sql"
    previous = source.read_text(encoding="utf-8")
    for name in (
        "jobseek_crawler_ownership_reversal_transition",
        "jobseek_crawler_ownership_journal_transition",
    ):
        marker = f"FUNCTION public.{name}()"
        offset = previous.index(marker)
        start = previous.rfind("CREATE", 0, offset)
        end = previous.index("$$;", offset) + 3
        statement = previous[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        op.execute(statement)
    op.execute(
        "ALTER TABLE public.crawler_ownership_reversal DROP CONSTRAINT "
        "crawler_ownership_reversal_phase_check"
    )
    op.execute(
        "ALTER TABLE public.crawler_ownership_reversal ADD CONSTRAINT "
        "crawler_ownership_reversal_phase_check CHECK(phase IN ('pending','reserved'))"
    )
    op.execute(
        "ALTER TABLE public.crawler_ownership_reversal DROP CONSTRAINT "
        "crawler_ownership_reversal_retirement_phase"
    )
    op.execute(
        "ALTER TABLE public.crawler_ownership_reversal ADD CONSTRAINT "
        "crawler_ownership_reversal_retirement_phase "
        "CHECK((phase='pending' AND retirement_epoch IS NULL) "
        "OR (phase='reserved' AND retirement_epoch IS NOT NULL))"
    )
    op.execute("DROP TABLE public.crawler_ownership_restoration_completion")
    op.execute("DROP FUNCTION public.jobseek_crawler_restoration_completion()")
    op.execute("DROP TABLE public.crawler_ownership_restoration_publication")
    op.execute("DROP FUNCTION public.jobseek_crawler_restoration_publication()")
    op.execute(
        "DROP FUNCTION public.jobseek_crawler_restoration_cold_context"
        "(public.crawler_ownership_restoration_finalization)"
    )
    op.execute("DROP TABLE public.crawler_ownership_restoration_finalization")
    op.execute("DROP FUNCTION public.jobseek_crawler_restoration_finalization()")
