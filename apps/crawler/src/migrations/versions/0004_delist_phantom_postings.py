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

Design notes:

1. Filters on ``board_status IN ('disabled','gone')``, NOT just
   ``is_enabled = false``. Some boards are flagged ``is_enabled=false``
   for non-dead reasons (manual ops pause, mid-reconfigure) and keep
   ``board_status = 'active'`` on the row — those should keep their
   postings live.

2. Chunked with ``op.get_context().autocommit_block()`` so each chunk
   commits independently. The default alembic ``env.py`` wraps the
   whole ``upgrade()`` in one transaction; an autocommit block breaks
   out of that transaction for the duration of the block. A single
   non-chunked ``UPDATE`` on ~20k rows would accumulate row-level
   locks for the whole run and block live crawler workers on
   ``_DIFF_BATCH.touched/relisted/foreign_touched`` (which don't use
   SKIP LOCKED). The autocommit form releases locks every ~1k rows,
   giving workers a natural window.

3. The inner ``FOR UPDATE OF jp SKIP LOCKED`` on the SELECT lets the
   migration coexist with any worker that IS holding a row lock
   (they'll be picked up on a later chunk).

Runtime-side note: the code path's 5-strike recency gate
(``_DELIST_AFTER_FAILURE_AGE = 24h``) protects against mass-delete
during transient provider outages, but has a known limitation — a
board that 5-strike-disables within 24h of its last success will have
its phantom postings skipped by the runtime delist and never re-caught
(since disabled boards aren't re-fetched). Those phantoms get swept
on the next run of this migration. For ongoing environments, schedule
a periodic re-run (e.g. weekly) against a snapshot of the schema or
ship a lightweight ``crawler sweep-phantoms`` CLI in a follow-up.

Downgrade is a no-op: we don't retain enough state to safely re-flip
rows to ``is_active=true`` (and we wouldn't want to — the boards are
still dead).

Revision ID: 0004
Create Date: 2026-04-22
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# Per-chunk size: small enough that a single UPDATE holds row locks
# for under a second, large enough that the migration finishes in
# minutes rather than hours on ~20k target rows.
_CHUNK = 1000

# Hard cap on loop iterations as a safety belt against a pathological
# edge case (concurrent flood of board disables keeping the predicate
# non-empty). ~20k expected; 500 * 1000 = 500k is well above any
# plausible real backlog.
_MAX_ITERATIONS = 500


def upgrade() -> None:
    stmt = text("""
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
    """)
    for _ in range(_MAX_ITERATIONS):
        # autocommit_block breaks out of alembic's outer transaction so
        # this UPDATE commits on its own, releasing row locks before
        # the next chunk acquires new ones.
        with op.get_context().autocommit_block():
            bind = op.get_bind()
            result = bind.execute(stmt, {"chunk": _CHUNK})
            if result.rowcount == 0:
                return


def downgrade() -> None:
    # No-op: we can't reliably identify which rows this migration
    # touched, and the boards are disabled upstream anyway.
    pass
