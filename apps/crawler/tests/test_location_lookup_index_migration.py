"""Contract checks for the case-insensitive location-name lookup index."""

from __future__ import annotations

import importlib
from contextlib import nullcontext
from unittest.mock import MagicMock

from src.core.location_resolve import _BACKFILL_LOOKUP_SQL


def test_location_backfill_query_matches_functional_covering_index(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0027_index_location_name_lower_lookup"
    )
    execute = MagicMock()
    context = MagicMock()
    context.autocommit_block.return_value = nullcontext()
    bind = MagicMock()
    bind.exec_driver_sql.return_value.scalar_one_or_none.return_value = "location_name"
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_context", MagicMock(return_value=context))
    monkeypatch.setattr(migration.op, "get_bind", MagicMock(return_value=bind))

    migration.upgrade()

    statements = [call.args[0] for call in execute.call_args_list]
    context.autocommit_block.assert_called_once_with()
    assert migration.revision == "0027"
    assert migration.down_revision == "0026"
    assert "WHERE lower(name) = ANY($1::text[])" in _BACKFILL_LOOKUP_SQL
    assert statements == [
        "DROP INDEX CONCURRENTLY IF EXISTS idx_location_name_lower_lookup",
        "CREATE INDEX CONCURRENTLY idx_location_name_lower_lookup "
        "ON location_name (lower(name)) INCLUDE (location_id)",
    ]


def test_location_lookup_migration_skips_fresh_crawler_schema(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0027_index_location_name_lower_lookup"
    )
    execute = MagicMock()
    context = MagicMock()
    bind = MagicMock()
    bind.exec_driver_sql.return_value.scalar_one_or_none.return_value = None
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_context", MagicMock(return_value=context))
    monkeypatch.setattr(migration.op, "get_bind", MagicMock(return_value=bind))

    migration.upgrade()

    execute.assert_not_called()
    context.autocommit_block.assert_not_called()


def test_location_lookup_index_downgrade_is_concurrent(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0027_index_location_name_lower_lookup"
    )
    execute = MagicMock()
    context = MagicMock()
    context.autocommit_block.return_value = nullcontext()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "get_context", MagicMock(return_value=context))

    migration.downgrade()

    context.autocommit_block.assert_called_once_with()
    execute.assert_called_once_with(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_location_name_lower_lookup"
    )
