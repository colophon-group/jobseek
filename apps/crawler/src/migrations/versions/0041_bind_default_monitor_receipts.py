"""Bind default monitor receipts independently of mutable runtime metadata.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable and additive: retained runtimes keep their existing column ABI.
    op.execute(
        "ALTER TABLE public.ordinary_worker_write_fence ADD COLUMN stable_config_sha256 TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.ordinary_worker_write_fence DROP COLUMN stable_config_sha256")
