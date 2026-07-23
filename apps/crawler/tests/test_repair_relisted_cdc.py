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
    _FETCH_SUPABASE_ACTIVE,
    _TOUCH_MISMATCHES,
    parse_aware_datetime,
    repair_relisted_cdc,
)


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


def test_relisted_cte_advances_cdc_timestamp() -> None:
    compact = " ".join(_DIFF_BATCH.split())
    start = compact.index("relisted AS (")
    end = compact.index("foreign_touched AS (", start)
    relisted = compact[start:end]
    assert "SET is_active = true" in relisted
    assert "updated_at = now()" in relisted


def test_repair_sql_contract_is_bounded_and_race_safe() -> None:
    assert "last_seen_at >= $2::timestamptz" in _FETCH_CANDIDATES
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
async def test_dry_run_detects_each_downstream_without_writes() -> None:
    both = uuid.uuid4()
    missing_supa = uuid.uuid4()
    missing_ts = uuid.uuid4()
    local = AsyncMock()
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
    )

    assert result.scanned == 3
    assert result.mismatched == 2
    assert result.supabase_mismatched == 1
    assert result.typesense_mismatched == 1
    assert result.touched == 0
    assert all(call.args[0] != _TOUCH_MISMATCHES for call in local.fetch.await_args_list)


@pytest.mark.asyncio
async def test_repair_touches_union_of_downstream_mismatches() -> None:
    both = uuid.uuid4()
    missing_both = uuid.uuid4()
    local = AsyncMock()
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
    local.fetch = AsyncMock(side_effect=[[]])

    await repair_relisted_cdc(
        local,
        AsyncMock(),
        since=datetime(2026, 7, 22, 23, 52, tzinfo=UTC),
        dry_run=True,
        typesense_client=client,
        cursor_fence_factory=tracking_fence,
    )

    assert events == ["fence-enter", "typesense-snapshot", "fence-exit"]
