"""Backfill: delist phantom active postings on disabled boards.

Before this revision, ``_RECORD_FAILURE`` would flip a board to
``is_enabled=false`` + ``board_status='disabled'`` after the 5-strike
threshold without touching its postings. Because
``_FETCH_DUE_BOARDS`` filters to ``board_status IN ('active','suspect')
AND is_enabled=true``, the postings belonging to those boards would
never be re-polled and never flip to ``is_active=false`` — leaving tens
of thousands of phantom "active" rows that still appeared in search
results. The companion code change (``just_disabled`` RETURNING in
``_RECORD_FAILURE`` + ``_delist_and_count_gone``) prevents new
accumulation; this migration catches up the existing backlog.

Downgrade is a no-op: we don't retain enough state to safely re-flip
rows to ``is_active=true`` (and we wouldn't want to — the boards are
still dead).

Revision ID: 0004
Create Date: 2026-04-22
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Also handles rows on boards whose ``is_enabled=false`` was set by
    # other paths (e.g. ``_RECORD_EMPTY_CHECK`` crossing its own
    # threshold before the delist branch existed, or manual SQL).
    op.execute("""
        UPDATE job_posting jp
        SET is_active = false,
            next_scrape_at = NULL,
            updated_at = now()
        FROM job_board jb
        WHERE jp.board_id = jb.id
          AND jp.is_active = true
          AND jb.is_enabled = false
    """)


def downgrade() -> None:
    # No-op: we can't reliably identify which rows this migration
    # touched, and the boards are disabled upstream anyway.
    pass
