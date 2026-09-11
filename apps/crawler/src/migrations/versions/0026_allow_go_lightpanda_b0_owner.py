"""Allow the exclusive Go owner in the retained Lightpanda B0 write fence.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-11
"""

from __future__ import annotations

import importlib

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_V0024 = importlib.import_module("src.migrations.versions.0024_add_lightpanda_b0_write_fence")
_FUNCTION_MARKER = "CREATE OR REPLACE FUNCTION public.jobseek_lightpanda_b0_require_write_fence"
_PYTHON_OWNER_FUNCTIONS = (
    _FUNCTION_MARKER
    + _V0024._INSTALL_WRITE_FENCE.split(  # noqa: SLF001
        _FUNCTION_MARKER, 1
    )[1]
)
_GO_OWNER_FUNCTIONS = _PYTHON_OWNER_FUNCTIONS.replace(
    "supplied_engine_owner IS DISTINCT FROM 'python'",
    "supplied_engine_owner NOT IN ('python', 'go')",
).replace(
    "AND supplied_shard_id IS DISTINCT FROM current_fence.shard_id",
    "AND (supplied_shard_id IS DISTINCT FROM current_fence.shard_id "
    "OR supplied_engine_owner IS DISTINCT FROM current_fence.engine_owner)",
)
_REFUSE_DOWNGRADE_WITH_GO_ROWS = (
    "DO $rollback$ BEGIN "
    "IF EXISTS (SELECT 1 FROM public.lightpanda_b0_write_fence WHERE engine_owner = 'go') "
    "THEN RAISE EXCEPTION 'Lightpanda B0 owner rollback refused: Go rows exist'; END IF; "
    "END $rollback$"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.lightpanda_b0_write_fence "
        "DROP CONSTRAINT lightpanda_b0_write_fence_engine_owner_check"
    )
    op.execute(
        "ALTER TABLE public.lightpanda_b0_write_fence "
        "ADD CONSTRAINT lightpanda_b0_write_fence_engine_owner_check "
        "CHECK (engine_owner IN ('python', 'go'))"
    )
    op.execute(_GO_OWNER_FUNCTIONS)


def downgrade() -> None:
    op.execute(_REFUSE_DOWNGRADE_WITH_GO_ROWS)
    op.execute(_PYTHON_OWNER_FUNCTIONS)
    op.execute(
        "ALTER TABLE public.lightpanda_b0_write_fence "
        "DROP CONSTRAINT lightpanda_b0_write_fence_engine_owner_check"
    )
    op.execute(
        "ALTER TABLE public.lightpanda_b0_write_fence "
        "ADD CONSTRAINT lightpanda_b0_write_fence_engine_owner_check "
        "CHECK (engine_owner = 'python')"
    )
