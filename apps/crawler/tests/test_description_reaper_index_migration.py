"""Contract checks for the R2 orphan-claim reaper index."""

from __future__ import annotations

import importlib
from contextlib import nullcontext
from unittest.mock import MagicMock


def test_description_reaper_index_is_partial_ordered_and_concurrent(monkeypatch) -> None:
    migration = importlib.import_module("src.migrations.versions.0029_index_description_reaper")
    execute = MagicMock()
    context = MagicMock()
    context.autocommit_block.return_value = nullcontext()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_context", MagicMock(return_value=context))

    migration.upgrade()

    context.autocommit_block.assert_called_once_with()
    assert migration.revision == "0029"
    assert migration.down_revision == "0028"
    assert [call.args[0] for call in execute.call_args_list] == [
        "DROP INDEX CONCURRENTLY IF EXISTS idx_desc_r2_claim_reaper",
        "CREATE INDEX CONCURRENTLY idx_desc_r2_claim_reaper "
        "ON descriptions (updated_at, posting_id, locale) "
        "WHERE r2_uploaded IS NULL",
    ]


def test_description_reaper_index_downgrade_is_concurrent(monkeypatch) -> None:
    migration = importlib.import_module("src.migrations.versions.0029_index_description_reaper")
    execute = MagicMock()
    context = MagicMock()
    context.autocommit_block.return_value = nullcontext()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_context", MagicMock(return_value=context))

    migration.downgrade()

    context.autocommit_block.assert_called_once_with()
    execute.assert_called_once_with("DROP INDEX CONCURRENTLY IF EXISTS idx_desc_r2_claim_reaper")
