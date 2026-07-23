"""Tests for the exporter/operator advisory cursor fence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.export_cursor_fence import (
    _CDC_CUTOFF_SQL,
    _TRY_LOCK_SQL,
    _UNLOCK_SQL,
    CDC_WRITER_BARRIER_ID,
    EXPORT_CURSOR_FENCE_ID,
    capture_cdc_snapshot_cutoff,
    export_cursor_fence,
)


def _pool_and_connection():
    pool = MagicMock()
    connection = AsyncMock()
    connection.fetchval = AsyncMock(side_effect=[True, True])
    connection.fetchrow = AsyncMock()
    connection.is_closed = MagicMock(return_value=False)

    def terminate() -> None:
        connection.is_closed.return_value = True

    connection.terminate = MagicMock(side_effect=terminate)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire
    return pool, connection


@pytest.mark.asyncio
async def test_fence_acquires_and_releases_same_session_lock() -> None:
    pool, connection = _pool_and_connection()

    async with export_cursor_fence(pool):
        connection.fetchval.assert_awaited_once_with(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID)

    assert connection.fetchval.await_args_list == [
        call(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID),
        call(_UNLOCK_SQL, EXPORT_CURSOR_FENCE_ID),
    ]
    connection.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_fence_releases_lock_when_guarded_work_fails() -> None:
    pool, connection = _pool_and_connection()

    with pytest.raises(RuntimeError, match="guarded failure"):
        async with export_cursor_fence(pool):
            raise RuntimeError("guarded failure")

    assert connection.method_calls == [
        call.fetchval(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID),
        call.fetchval(_UNLOCK_SQL, EXPORT_CURSOR_FENCE_ID),
    ]


@pytest.mark.asyncio
async def test_fence_polls_without_blocking_command_timeout(monkeypatch) -> None:
    pool, connection = _pool_and_connection()
    connection.fetchval.side_effect = [False, False, True, True]
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    async with export_cursor_fence(pool):
        pass

    assert connection.fetchval.await_args_list == [
        call(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID),
        call(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID),
        call(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID),
        call(_UNLOCK_SQL, EXPORT_CURSOR_FENCE_ID),
    ]
    assert sleep.await_count == 2
    connection.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_contending_fence_enters_only_after_owner_releases(monkeypatch) -> None:
    owner: str | None = None
    events: list[str] = []
    waiter_observed = asyncio.Event()
    owner_released = asyncio.Event()

    def contender(name: str):
        async def fetchval(query: str, lock_id: int) -> bool:
            nonlocal owner
            assert lock_id == EXPORT_CURSOR_FENCE_ID
            if query == _TRY_LOCK_SQL:
                if owner is None:
                    owner = name
                    events.append(f"{name}-acquired")
                    return True
                events.append(f"{name}-waiting")
                waiter_observed.set()
                return False
            assert query == _UNLOCK_SQL
            assert owner == name
            owner = None
            events.append(f"{name}-released")
            return True

        pool, connection = _pool_and_connection()
        connection.fetchval.side_effect = fetchval
        return pool

    async def controlled_sleep(_seconds: float) -> None:
        await owner_released.wait()

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    first = export_cursor_fence(contender("first"))
    await first.__aenter__()

    async def enter_second() -> None:
        async with export_cursor_fence(contender("second")):
            events.append("second-entered")

    second_task = asyncio.create_task(enter_second())
    await asyncio.wait_for(waiter_observed.wait(), timeout=1)
    assert "second-entered" not in events

    await first.__aexit__(None, None, None)
    owner_released.set()
    await asyncio.wait_for(second_task, timeout=1)

    assert events == [
        "first-acquired",
        "second-waiting",
        "first-released",
        "second-acquired",
        "second-entered",
        "second-released",
    ]


@pytest.mark.asyncio
async def test_fence_terminates_uncertain_session_when_acquisition_is_cancelled() -> None:
    pool, connection = _pool_and_connection()
    connection.fetchval.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        async with export_cursor_fence(pool):
            pass

    connection.terminate.assert_called_once_with()


@pytest.mark.asyncio
async def test_fence_terminates_uncertain_session_when_release_is_cancelled() -> None:
    pool, connection = _pool_and_connection()
    connection.fetchval.side_effect = [True, asyncio.CancelledError]

    with pytest.raises(asyncio.CancelledError):
        async with export_cursor_fence(pool):
            pass

    connection.terminate.assert_called_once_with()


@pytest.mark.asyncio
async def test_fence_terminates_session_when_unlock_reports_not_held() -> None:
    pool, connection = _pool_and_connection()
    connection.fetchval.side_effect = [True, False]

    with pytest.raises(RuntimeError, match="was not held"):
        async with export_cursor_fence(pool):
            pass

    connection.terminate.assert_called_once_with()


@pytest.mark.asyncio
async def test_cdc_cutoff_uses_captured_clock_without_active_writers() -> None:
    pool, connection = _pool_and_connection()
    cutoff = datetime(2026, 7, 23, 6, 30, tzinfo=UTC)
    connection.fetchrow.return_value = {
        "captured_at": cutoff,
        "cutoff": cutoff,
        "active_writers": 0,
        "unknown_writers": 0,
    }

    observed = await capture_cdc_snapshot_cutoff(pool)

    assert observed == cutoff
    connection.fetchrow.assert_awaited_once_with(_CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID)
    connection.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_cdc_cutoff_uses_oldest_active_writer_floor_without_waiting() -> None:
    pool, connection = _pool_and_connection()
    captured = datetime(2026, 7, 23, 6, 31, tzinfo=UTC)
    cutoff = datetime(2026, 7, 23, 6, 30, 50, tzinfo=UTC)
    connection.fetchrow.return_value = {
        "captured_at": captured,
        "cutoff": cutoff,
        "active_writers": 7,
        "unknown_writers": 0,
    }

    assert await capture_cdc_snapshot_cutoff(pool) == cutoff

    connection.fetchrow.assert_awaited_once_with(_CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID)
    connection.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_cdc_cutoff_fails_closed_for_unknown_writer_transaction() -> None:
    pool, connection = _pool_and_connection()
    captured = datetime(2026, 7, 23, 6, 31, tzinfo=UTC)
    connection.fetchrow.return_value = {
        "captured_at": captured,
        "cutoff": captured,
        "active_writers": 1,
        "unknown_writers": 1,
    }

    with pytest.raises(RuntimeError, match="transaction start is unavailable"):
        await capture_cdc_snapshot_cutoff(pool)

    connection.fetchrow.assert_awaited_once_with(_CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID)


@pytest.mark.asyncio
async def test_cdc_cutoff_rejects_missing_result() -> None:
    pool, connection = _pool_and_connection()
    connection.fetchrow.return_value = None

    with pytest.raises(RuntimeError, match="returned no CDC cutoff"):
        await capture_cdc_snapshot_cutoff(pool)


def test_cdc_cutoff_sql_captures_clock_before_inspecting_bigint_lock_holders() -> None:
    compact = " ".join(_CDC_CUTOFF_SQL.split())

    assert "captured AS MATERIALIZED ( SELECT clock_timestamp()" in compact
    assert "FROM captured JOIN pg_locks" in compact
    assert "locks.objsubid = 1" in compact
    assert "locks.database =" in compact
    assert "min(writers.xact_start)" in compact
