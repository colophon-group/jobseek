"""Regression contracts for confirmed-empty board recovery."""

from __future__ import annotations

import runpy
from pathlib import Path

from src.queries.monitor import _RECORD_EMPTY_CHECK


def test_successful_empty_check_never_retires_the_board() -> None:
    """Empty results may delist postings but must preserve future polling."""
    assert "THEN 'gone'" not in _RECORD_EMPTY_CHECK
    assert "THEN false" not in _RECORD_EMPTY_CHECK
    assert "THEN 'suspect'" in _RECORD_EMPTY_CHECK
    assert "empty_check_count >= 6 AS should_delist" in _RECORD_EMPTY_CHECK


def test_recovery_migration_targets_only_legacy_empty_retirements() -> None:
    migration = (
        Path(__file__).parents[1]
        / "src"
        / "migrations"
        / "versions"
        / "0011_reactivate_empty_boards.py"
    )
    sql = runpy.run_path(str(migration))["_REACTIVATE_EMPTY_BOARDS"]

    assert "board_status = 'gone'" in sql
    assert "is_enabled = false" in sql
    assert "consecutive_failures = 0" in sql
    assert "last_error IS NULL" in sql
    assert "last_non_empty_at IS NOT NULL" in sql
    assert "empty_check_count >= 6" in sql
    assert "board_status = 'suspect'" in sql
    assert "is_enabled = true" in sql
