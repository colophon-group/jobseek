"""Retain learned host routing in ordinary terminal receipts.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).parent.parent / "sql" / "ordinary_worker_receipt_host.sql"
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE public.ordinary_worker_write_fence DROP COLUMN learned_egress_host")
