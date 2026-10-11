"""Retain the next native detail step with its completed write fence.

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


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "ordinary_detail_handoff.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.ordinary_worker_write_fence "
        "DROP COLUMN next_detail_step, DROP COLUMN next_detail_at, "
        "DROP COLUMN next_detail_description_hash"
    )
