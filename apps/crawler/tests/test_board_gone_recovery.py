"""Regression contracts for recoverable provider-gone confirmation (#6156)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

from src.processing.gone_policy import (
    GONE_CONFIRMATION_SPACING,
    GONE_RECOVERY_INTERVAL,
    evaluate_gone_confirmation,
)
from src.queries.monitor import (
    _FETCH_BOARD_GONE_STATE,
    _FETCH_DUE_BOARDS,
    _RECORD_BOARD_GONE,
    _RECORD_EMPTY_CHECK,
    _RECORD_SUCCESS_NONEMPTY,
)
from src.sync import (
    _FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP,
    _RECOVERY_SCHEDULE_STATUSES,
    _UPSERT_BOARD_LOCAL,
)

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)


def _decision(
    *,
    status: str = "active",
    count: int = 0,
    first: datetime | None = None,
    last: datetime | None = None,
    success: datetime | None = None,
    gone_at: datetime | None = None,
    now: datetime = NOW,
):
    return evaluate_gone_confirmation(
        board_status=status,
        confirmation_count=count,
        first_confirmed_at=first,
        last_confirmed_at=last,
        last_success_at=success,
        gone_at=gone_at,
        now=now,
    )


def test_one_404_after_recent_success_is_pending_and_cannot_delist() -> None:
    decision = _decision(success=NOW - timedelta(hours=1))

    assert decision.board_status == "gone_pending"
    assert decision.confirmation_count == 1
    assert decision.required_confirmations == 3
    assert decision.confirmation_advanced is True
    assert decision.terminal_transition is False
    assert decision.gone_at is None
    assert decision.next_check_at == NOW + GONE_CONFIRMATION_SPACING


def test_duplicate_404_inside_spacing_window_does_not_advance() -> None:
    first = NOW - timedelta(hours=1)
    decision = _decision(
        status="gone_pending",
        count=1,
        first=first,
        last=first,
        success=NOW - timedelta(hours=2),
    )

    assert decision.confirmation_count == 1
    assert decision.confirmation_advanced is False
    assert decision.terminal_transition is False
    assert decision.next_check_at == first + GONE_CONFIRMATION_SPACING


def test_three_spaced_404s_retire_recently_healthy_board() -> None:
    first_at = NOW - 2 * GONE_CONFIRMATION_SPACING
    second_at = NOW - GONE_CONFIRMATION_SPACING
    decision = _decision(
        status="gone_pending",
        count=2,
        first=first_at,
        last=second_at,
        success=first_at - timedelta(hours=1),
    )

    assert decision.board_status == "gone"
    assert decision.confirmation_count == 3
    assert decision.required_confirmations == 3
    assert decision.terminal_transition is True
    assert decision.gone_at == NOW
    assert decision.next_check_at == NOW + GONE_RECOVERY_INTERVAL


def test_two_spaced_404s_can_retire_long_inactive_board() -> None:
    first_at = NOW - GONE_CONFIRMATION_SPACING
    decision = _decision(
        status="gone_pending",
        count=1,
        first=first_at,
        last=first_at,
        success=NOW - timedelta(days=30),
    )

    assert decision.required_confirmations == 2
    assert decision.terminal_transition is True


def test_confirmed_gone_board_keeps_daily_recovery_probe() -> None:
    yesterday = NOW - GONE_RECOVERY_INTERVAL
    decision = _decision(
        status="gone",
        count=3,
        first=NOW - timedelta(days=2),
        last=yesterday,
        gone_at=NOW - timedelta(days=1),
    )

    assert decision.board_status == "gone"
    assert decision.confirmation_advanced is True
    assert decision.terminal_transition is False
    assert decision.next_check_at == NOW + GONE_RECOVERY_INTERVAL


def test_new_404_after_reappearance_starts_a_fresh_episode() -> None:
    old_confirmation = NOW - timedelta(hours=1)
    decision = _decision(
        status="active",
        count=0,
        first=NOW - timedelta(days=2),
        last=old_confirmation,
        success=NOW - timedelta(minutes=30),
    )

    assert decision.confirmation_advanced is True
    assert decision.confirmation_count == 1
    assert decision.first_confirmed_at == NOW
    assert decision.last_confirmed_at == NOW


def test_state_queries_persist_evidence_and_success_self_recovers() -> None:
    fetch_sql = " ".join(_FETCH_BOARD_GONE_STATE.split())
    record_sql = " ".join(_RECORD_BOARD_GONE.split())

    assert "FOR UPDATE" in fetch_sql
    for column in (
        "gone_confirmation_count",
        "gone_first_confirmed_at",
        "gone_last_confirmed_at",
        "last_gone_error",
        "last_gone_endpoint",
        "last_gone_status",
        "gone_transition_count",
    ):
        assert column in record_sql
    assert "is_enabled = true" in record_sql
    assert "is_enabled = false" not in record_sql

    for success_sql in (_RECORD_SUCCESS_NONEMPTY, _RECORD_EMPTY_CHECK):
        assert "IN ('gone_pending', 'gone')" in success_sql
        assert "gone_recovery_count" in success_sql
        assert "gone_confirmation_count = 0" in success_sql
        assert "gone_at = NULL" in success_sql
        assert "'gone'" in success_sql


def test_legacy_migration_reactivates_all_one_shot_gone_rows_with_splay() -> None:
    migration = importlib.import_module("src.migrations.versions.0016_confirm_board_gone")
    sql = " ".join(migration._REACTIVATE_LEGACY_GONE_BOARDS.split())

    assert migration.down_revision == "0015"
    assert "WHERE board_status = 'gone'" in sql
    assert "board_status = 'gone_pending'" in sql
    assert "is_enabled = true" in sql
    assert "gone_confirmation_count" in sql
    assert "6156" in sql
    assert "900" in sql


def test_redis_lifecycle_keeps_pending_and_terminal_gone_boards_recurring() -> None:
    assert {"quarantined", "gone_pending", "gone"} == _RECOVERY_SCHEDULE_STATUSES
    assert "'gone_pending', 'gone'" in _FETCH_DUE_BOARDS
    assert "board_status = 'disabled'" in _FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP
    assert "board_status IN ('disabled', 'gone')" not in _FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP
    assert "WHEN job_board.board_status = 'disabled' THEN false" in _UPSERT_BOARD_LOCAL
