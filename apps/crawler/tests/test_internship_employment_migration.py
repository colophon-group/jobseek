"""Regression contract for moving internship into seniority."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock


def test_migration_moves_internship_to_seniority_and_clears_employment_type() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0025_drop_internship_employment_type"
    )
    sql = migration._MIGRATE_INTERNSHIP_TO_SENIORITY

    assert migration.revision == "0025"
    assert migration.down_revision == "0024"
    assert "WHERE slug = 'intern'" in sql
    assert "seniority_id = intern_seniority_id" in sql
    assert "employment_type = NULL" in sql
    assert "WHERE employment_type = 'internship'" in sql
    assert "updated_at = now()" in sql

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    execute.assert_called_once_with(sql)
