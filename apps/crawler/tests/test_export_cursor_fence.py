"""Tests for the exporter/operator advisory cursor fence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.export_cursor_fence import EXPORT_CURSOR_FENCE_ID, export_cursor_fence


def _pool_and_connection():
    pool = MagicMock()
    connection = AsyncMock()
    connection.fetchval = AsyncMock(return_value=True)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire
    return pool, connection


@pytest.mark.asyncio
async def test_fence_acquires_and_releases_same_session_lock() -> None:
    pool, connection = _pool_and_connection()

    async with export_cursor_fence(pool):
        connection.execute.assert_awaited_once_with(
            "SELECT pg_advisory_lock($1::bigint)",
            EXPORT_CURSOR_FENCE_ID,
        )

    connection.fetchval.assert_awaited_once_with(
        "SELECT pg_advisory_unlock($1::bigint)",
        EXPORT_CURSOR_FENCE_ID,
    )


@pytest.mark.asyncio
async def test_fence_releases_lock_when_guarded_work_fails() -> None:
    pool, connection = _pool_and_connection()

    with pytest.raises(RuntimeError, match="guarded failure"):
        async with export_cursor_fence(pool):
            raise RuntimeError("guarded failure")

    assert connection.method_calls == [
        call.execute("SELECT pg_advisory_lock($1::bigint)", EXPORT_CURSOR_FENCE_ID),
        call.fetchval("SELECT pg_advisory_unlock($1::bigint)", EXPORT_CURSOR_FENCE_ID),
    ]
