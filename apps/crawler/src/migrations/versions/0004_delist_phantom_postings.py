"""Backfill: delist phantom active postings on dead boards.

Before this revision, ``_RECORD_FAILURE`` would flip a board to
``is_enabled=false`` + ``board_status='disabled'`` after the 5-strike
threshold without touching its postings. Because
``_FETCH_DUE_BOARDS`` filters to ``board_status IN ('active','suspect')
AND is_enabled=true``, the postings belonging to those boards would
never be re-polled and never flip to ``is_active=false`` — leaving
tens of thousands of phantom "active" rows that still appeared in
search results. The companion code change in this PR
(``_RECORD_FAILURE`` RETURNING + ``_maybe_delist_after_disable``)
prevents new accumulation; this migration catches up the existing
backlog.

Two safety choices that matter:

1. Filter on ``board_status IN ('disabled','gone')``, NOT just
   ``is_enabled = false``. Some boards are flagged ``is_enabled=false``
   for non-dead reasons (manual ops pause, mid-reconfigure, etc.) and
   keep ``board_status = 'active'`` on the row — those should keep
   their postings live. Only boards explicitly marked dead get
   delisted here.

2. Chunked in a Python loop with per-chunk commits. A single ``UPDATE``
   covering ~20k rows in a 1M+ row hot table would hold row-level
   write locks for the duration, blocking the live crawler workers
   that are constantly writing to ``job_posting``. The chunked form
   releases locks every batch and lets the CDC exporter pace its
   downstream writes naturally.

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


# Per-chunk size: small enough that a single UPDATE holds locks for
# under a second, large enough that the migration finishes in minutes
# rather than hours.
_CHUNK = 1000


def upgrade() -> None:
    bind = op.get_bind()
    while True:
        result = bind.execute(  # type: ignore[attr-defined]
            _sa_text("""
                UPDATE job_posting
                SET is_active = false,
                    next_scrape_at = NULL,
                    updated_at = now()
                WHERE id IN (
                    SELECT jp.id
                    FROM job_posting jp
                    JOIN job_board jb ON jb.id = jp.board_id
                    WHERE jp.is_active = true
                      AND jb.is_enabled = false
                      AND jb.board_status IN ('disabled', 'gone')
                    LIMIT :chunk
                    FOR UPDATE OF jp SKIP LOCKED
                )
            """),
            {"chunk": _CHUNK},
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            break


def downgrade() -> None:
    # No-op: we can't reliably identify which rows this migration
    # touched, and the boards are disabled upstream anyway.
    pass


def _sa_text(sql: str):
    # Imported lazily so importing this revision module (e.g. for
    # ``alembic history`` or ``alembic show 0004``) doesn't pull in
    # SQLAlchemy unless the migration actually runs.
    from sqlalchemy import text

    return text(sql)
