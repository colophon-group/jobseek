"""Static contracts for the commit-safe posting CDC migration."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from src.export_cursor_fence import CDC_WRITER_BARRIER_ID
from src.exporter import PostingSchema


def _migration_module():
    return importlib.import_module("src.migrations.versions.0012_commit_safe_posting_cdc")


def test_migration_and_runtime_use_the_same_advisory_lock_id() -> None:
    migration = _migration_module()

    assert migration._CDC_WRITER_BARRIER_ID == CDC_WRITER_BARRIER_ID


def test_migration_covers_every_mutable_downstream_posting_field() -> None:
    migration = _migration_module()

    assert set(migration._EXPORTED_MUTABLE_COLUMNS) == set(PostingSchema.upsert_columns)
    assert set(migration._TRIGGER_UPDATE_COLUMNS) == {
        *PostingSchema.upsert_columns,
        "updated_at",
    }


def test_migration_installs_statement_lock_before_row_clock_stamp() -> None:
    migration = _migration_module()
    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
    finally:
        migration.op = original_op

    statements = "\n".join(call.args[0] for call in execute.call_args_list)
    assert "pg_advisory_xact_lock_shared" in statements
    assert "clock_timestamp()" in statements
    assert "FOR EACH STATEMENT" in statements
    assert "FOR EACH ROW" in statements
    assert statements.index("job_posting_cdc_lock_insert") < statements.index(
        "job_posting_cdc_stamp_insert"
    )
