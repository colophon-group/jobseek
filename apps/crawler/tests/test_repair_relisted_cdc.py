"""Tests for relisted posting CDC repair."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.queries.monitor import _DIFF_BATCH
from src.repair_relisted_cdc import (
    _FETCH_CANDIDATES,
    _FETCH_REPAIR_CUTOFF,
    _FETCH_SUPABASE_ACTIVE,
    _TOUCH_MISMATCHES,
    parse_aware_datetime,
    repair_candidate_snapshot,
    repair_relisted_cdc,
)

_CUTOFF = datetime(2026, 7, 23, 1, 43, tzinfo=UTC)


class _Documents:
    def __init__(self, active_ids: set[str]):
        self.active_ids = active_ids

    def export(self, params: dict) -> str:
        assert params == {"filter_by": "is_active:=true", "include_fields": "id"}
        return "\n".join(json.dumps({"id": posting_id}) for posting_id in self.active_ids)


class _Collection:
    def __init__(self, active_ids: set[str]):
        self.documents = _Documents(active_ids)


class _Collections:
    def __init__(self, active_ids: set[str]):
        self.active_ids = active_ids

    def __getitem__(self, name: str) -> _Collection:
        assert name == "job_posting"
        return _Collection(self.active_ids)


def _client(active_ids: set[uuid.UUID]) -> SimpleNamespace:
    return SimpleNamespace(collections=_Collections({str(posting_id) for posting_id in active_ids}))


@asynccontextmanager
async def _noop_cursor_fence(_pool):
    yield


@asynccontextmanager
async def _candidate_snapshot(local):
    yield local


def test_relisted_cte_advances_cdc_timestamp() -> None:
    compact = " ".join(_DIFF_BATCH.split())
    start = compact.index("relisted AS (")
    end = compact.index("foreign_touched AS (", start)
    relisted = compact[start:end]
    assert "SET is_active = true" in relisted
    assert "updated_at = now()" in relisted


def test_repair_sql_contract_is_bounded_and_race_safe() -> None:
    assert "last_seen_at >= $2::timestamptz" in _FETCH_CANDIDATES
    assert "updated_at < $3::timestamptz" in _FETCH_CANDIDATES
    assert "LIMIT $4" in _FETCH_CANDIDATES
    assert "updated_at < last_seen_at" not in _FETCH_CANDIDATES
    assert "id > $1::uuid" in _FETCH_CANDIDATES
    assert "is_active = true" in _FETCH_SUPABASE_ACTIVE
    assert "SET updated_at = now()" in _TOUCH_MISMATCHES
    assert "AND is_active = true" in _TOUCH_MISMATCHES


def test_parse_since_requires_timezone() -> None:
    assert parse_aware_datetime("2026-07-22T23:52:00Z").tzinfo is not None
    with pytest.raises(ValueError, match="timezone"):
        parse_aware_datetime("2026-07-22T23:52:00")


@pytest.mark.asyncio
async def test_candidate_scan_uses_one_read_only_repeatable_read_snapshot() -> None:
    events: list[object] = []

    class Transaction:
        async def __aenter__(self):
            events.append("transaction-enter")

        async def __aexit__(self, *_exc):
            events.append("transaction-exit")

    class Connection:
        def transaction(self, **kwargs):
            events.append(kwargs)
            return Transaction()

    connection = Connection()

    class Acquire:
        async def __aenter__(self):
            events.append("connection-enter")
            return connection

        async def __aexit__(self, *_exc):
            events.append("connection-exit")

    pool = SimpleNamespace(acquire=lambda: Acquire())

    async with repair_candidate_snapshot(pool) as yielded:
        assert yielded is connection
        events.append("yield")

    assert events == [
        "connection-enter",
        {"isolation": "repeatable_read", "readonly": True},
        "transaction-enter",
        "yield",
        "transaction-exit",
        "connection-exit",
    ]


@pytest.mark.asyncio
async def test_dry_run_detects_each_downstream_without_writes() -> None:
    both = uuid.uuid4()
    missing_supa = uuid.uuid4()
    missing_ts = uuid.uuid4()
    local = AsyncMock()
    local.fetchval = AsyncMock(return_value=_CUTOFF)
    local.fetch = AsyncMock(
        side_effect=[
            [{"id": both}, {"id": missing_supa}, {"id": missing_ts}],
            [],
        ]
    )
    supa = AsyncMock()
    supa.fetch = AsyncMock(return_value=[{"id": both}, {"id": missing_ts}])

    result = await repair_relisted_cdc(
        local,
        supa,
        since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
        dry_run=True,
        typesense_client=_client({both, missing_supa}),
        cursor_fence_factory=_noop_cursor_fence,
        candidate_snapshot_factory=_candidate_snapshot,
    )

    assert result.scanned == 3
    assert result.mismatched == 2
    assert result.supabase_mismatched == 1
    assert result.typesense_mismatched == 1
    assert result.touched == 0
    local.fetchval.assert_awaited_once_with(_FETCH_REPAIR_CUTOFF)
    assert local.fetch.await_args_list[0].args == (
        _FETCH_CANDIDATES,
        uuid.UUID(int=0),
        datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
        _CUTOFF,
        2000,
    )
    assert all(call.args[0] != _TOUCH_MISMATCHES for call in local.fetch.await_args_list)


@pytest.mark.asyncio
async def test_repair_touches_union_of_downstream_mismatches() -> None:
    both = uuid.uuid4()
    missing_both = uuid.uuid4()
    local = AsyncMock()
    local.fetchval = AsyncMock(return_value=_CUTOFF)
    local.fetch = AsyncMock(
        side_effect=[
            [{"id": both}, {"id": missing_both}],
            [{"id": missing_both}],
            [],
        ]
    )
    supa = AsyncMock()
    supa.fetch = AsyncMock(return_value=[{"id": both}])

    result = await repair_relisted_cdc(
        local,
        supa,
        since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
        typesense_client=_client({both}),
        cursor_fence_factory=_noop_cursor_fence,
        candidate_snapshot_factory=_candidate_snapshot,
    )

    assert result.mismatched == 1
    assert result.touched == 1
    touch_call = next(
        call for call in local.fetch.await_args_list if call.args[0] == _TOUCH_MISMATCHES
    )
    assert touch_call.args[1] == [missing_both]


@pytest.mark.asyncio
async def test_repair_fails_closed_without_typesense() -> None:
    with pytest.raises(RuntimeError, match="Typesense"):
        await repair_relisted_cdc(
            AsyncMock(),
            AsyncMock(),
            since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
            typesense_client=None,
            cursor_fence_factory=_noop_cursor_fence,
        )


@pytest.mark.asyncio
async def test_repair_holds_cursor_fence_during_snapshot_and_scan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def tracking_fence(_pool):
        events.append("fence-enter")
        try:
            yield
        finally:
            events.append("fence-exit")

    @asynccontextmanager
    async def tracking_snapshot(candidate_conn):
        events.append("snapshot-enter")
        try:
            yield candidate_conn
        finally:
            events.append("snapshot-exit")

    class TrackingDocuments(_Documents):
        def export(self, params: dict) -> str:
            events.append("typesense-snapshot")
            return super().export(params)

    class TrackingCollections:
        def __init__(self) -> None:
            self.collection = SimpleNamespace(documents=TrackingDocuments(set()))

        def __getitem__(self, name: str) -> SimpleNamespace:
            assert name == "job_posting"
            return self.collection

    client = SimpleNamespace(collections=TrackingCollections())
    local = AsyncMock()
    local.fetchval = AsyncMock(return_value=_CUTOFF)
    local.fetch = AsyncMock(side_effect=[[]])

    await repair_relisted_cdc(
        local,
        AsyncMock(),
        since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
        dry_run=True,
        typesense_client=client,
        cursor_fence_factory=tracking_fence,
        candidate_snapshot_factory=tracking_snapshot,
    )

    assert events == [
        "fence-enter",
        "snapshot-enter",
        "typesense-snapshot",
        "snapshot-exit",
        "fence-exit",
    ]


@pytest.mark.asyncio
async def test_repair_fails_closed_on_invalid_database_cutoff() -> None:
    local = AsyncMock()
    local.fetchval = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="invalid repair cutoff"):
        await repair_relisted_cdc(
            local,
            AsyncMock(),
            since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
            dry_run=True,
            typesense_client=_client(set()),
            cursor_fence_factory=_noop_cursor_fence,
            candidate_snapshot_factory=_candidate_snapshot,
        )
