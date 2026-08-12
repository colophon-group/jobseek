"""Regression contracts for recoverable configured-board quarantine (#6157)."""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

from src.queries.monitor import (
    _FETCH_DUE_BOARDS,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SUCCESS_NONEMPTY,
)
from src.sync import (
    _DISABLE_REMOVED_BOARDS,
    _DISABLE_REMOVED_BOARDS_LOCAL,
    _FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP,
    _MONITOR_CONFIG_FINGERPRINT,
    _UPSERT_BOARD_LOCAL,
)


def test_five_strikes_enter_daily_capped_recoverable_quarantine() -> None:
    sql = " ".join(_RECORD_FAILURE.split())

    assert "is_enabled = true" in sql
    assert "THEN 'quarantined'" in sql
    assert "LEAST( 5 * pow(2, LEAST(jb.consecutive_failures, 9)), 1440 )" in sql
    assert "* interval '1 minute'" in sql
    assert "|| ' minutes'" not in sql
    assert "AS next_failure_count" in sql
    assert "consecutive_failures = previous.next_failure_count" in sql
    assert "jb.consecutive_failures + 1" not in sql
    assert "entered_quarantine" in sql
    assert "last_quarantined_at" in sql
    assert "last_quarantine_error" in sql
    assert "quarantine_probe_count" in sql
    assert "THEN 'disabled'" not in sql
    assert "is_enabled = false" not in sql
    assert "last_success_at <" not in sql


def test_quarantined_boards_remain_due_and_success_self_recovers() -> None:
    assert "'active', 'suspect', 'quarantined', 'gone_pending', 'gone'" in _FETCH_DUE_BOARDS
    for sql in (_RECORD_SUCCESS_NONEMPTY, _RECORD_EMPTY_CHECK):
        assert "previous.board_status = 'quarantined' THEN 'quarantined'" in sql
        assert "AS recovered_from" in sql
        assert "last_recovered_at" in sql
        assert "recovery_count" in sql
        assert "quarantined_at = NULL" in sql
        assert "quarantine_probe_count = 0" in sql


def test_legacy_disable_migration_prioritizes_ashby_and_splays_the_rest() -> None:
    migration = importlib.import_module("src.migrations.versions.0015_recover_disabled_boards")
    sql = " ".join(migration._REACTIVATE_DISABLED_BOARDS.split())

    assert migration.down_revision == "0014"
    assert "WHERE board_status = 'disabled' AND is_enabled = false" in sql
    assert "board_status = 'quarantined'" in sql
    assert "is_enabled = true" in sql
    assert "WHEN crawler_type = 'ashby' THEN interval '0 seconds'" in sql
    assert "21600" in sql
    assert "board_status = 'gone'" not in sql


def test_sync_detects_monitor_contract_repairs_without_retry_storms() -> None:
    assert _MONITOR_CONFIG_FINGERPRINT == "_monitor_config_fingerprint"
    assert "job_board.metadata ? '_monitor_config_fingerprint'" in _UPSERT_BOARD_LOCAL
    assert "THEN 'quarantined'" in _UPSERT_BOARD_LOCAL
    assert "metadata, next_check_at, board_status" in _UPSERT_BOARD_LOCAL
    assert "quarantined_at" not in _DISABLE_REMOVED_BOARDS
    assert "lease_owner" not in _DISABLE_REMOVED_BOARDS
    assert "leased_until" not in _DISABLE_REMOVED_BOARDS
    assert "quarantined_at = NULL" in _DISABLE_REMOVED_BOARDS_LOCAL
    assert "lease_owner = NULL" in _DISABLE_REMOVED_BOARDS_LOCAL
    assert "leased_until = NULL" in _DISABLE_REMOVED_BOARDS_LOCAL
    assert "'quarantined'" not in _FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP


def test_quarantine_alerts_are_owned_and_have_runbooks() -> None:
    alerts_path = Path(__file__).parents[1] / "alerts.yaml"
    groups = yaml.safe_load(alerts_path.read_text(encoding="utf-8"))["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    names = {
        "CrawlerBoardQuarantineSchemaMissing",
        "CrawlerBoardQuarantineHasActivePostings",
        "CrawlerBoardQuarantineStale",
        "CrawlerBoardQuarantineRecoveryStalled",
        "CrawlerBoardGoneSchemaMissing",
        "CrawlerBoardGonePendingStale",
        "CrawlerBoardGoneTransitionSpike",
    }
    selected = [rule for rule in rules if rule.get("alert") in names]

    assert {rule["alert"] for rule in selected} == names
    for rule in selected:
        assert rule["labels"]["owner"] == "codex-error-review"
        assert rule["labels"]["route"] == "codex-daily"
        assert rule["annotations"]["runbook"].endswith(
            ("#board-quarantine-recovery", "#provider-gone-confirmation-and-recovery")
        )
