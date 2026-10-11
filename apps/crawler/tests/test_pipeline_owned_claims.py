from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ordinary_ownership import LegacyOwnership, OrdinaryOwnershipError
from src.redis_queue import BoardWork, WorkItem
from src.workers import pipeline


@pytest.mark.parametrize("browser", [False, True])
async def test_owned_empty_poll_is_shared_by_all_discovery_workers(monkeypatch, browser):
    expected = LegacyOwnership("a" * 64, "b" * 40, "c" * 40, "7")
    entered = 0

    @asynccontextmanager
    async def barrier(pool, ownership):
        nonlocal entered
        assert ownership == expected
        entered += 1
        yield

    async def empty(**kwargs):
        assert kwargs == {"browser": browser, "ownership": expected}
        await asyncio.sleep(0)
        return None

    claim = AsyncMock(side_effect=empty)
    monkeypatch.setattr(pipeline, "legacy_ownership_barrier", barrier)
    monkeypatch.setattr(pipeline, "claim_work", claim)
    gate = pipeline._LegacyClaimGate(MagicMock(), expected, browser=browser)
    assert await asyncio.gather(*(gate.claim() for _ in range(20))) == [None] * 20
    assert entered == claim.await_count == 1
    # A new polling period takes a fresh barrier; no claim authority is cached.
    gate.empty_until = 0
    assert await gate.claim() is None
    assert entered == claim.await_count == 2


async def test_owned_claim_gate_preserves_all_processing_slots(monkeypatch):
    expected = LegacyOwnership("a" * 64, "b" * 40, "c" * 40, "7")
    entered = 0

    @asynccontextmanager
    async def barrier(*args):
        nonlocal entered
        entered += 1
        yield

    async def available(**kwargs):
        await asyncio.sleep(0)
        return WorkItem(kind="monitor", board_work=BoardWork(str(entered), {}))

    monkeypatch.setattr(pipeline, "legacy_ownership_barrier", barrier)
    monkeypatch.setattr(pipeline, "claim_work", available)
    gate = pipeline._LegacyClaimGate(MagicMock(), expected, browser=False)
    results = await asyncio.gather(*(gate.claim() for _ in range(20)))
    assert {work.task_id for work in results} == {str(i) for i in range(1, 21)}
    assert entered == 20


@pytest.mark.parametrize("failure", [OrdinaryOwnershipError(), asyncio.CancelledError()])
async def test_owned_poll_failure_and_cancellation_release_gate(monkeypatch, failure):
    @asynccontextmanager
    async def barrier(*args):
        raise failure
        yield

    claim = AsyncMock()
    monkeypatch.setattr(pipeline, "legacy_ownership_barrier", barrier)
    monkeypatch.setattr(pipeline, "claim_work", claim)
    gate = pipeline._LegacyClaimGate(
        MagicMock(), LegacyOwnership("a" * 64, "b" * 40, "c" * 40, "7"), browser=False
    )
    with pytest.raises(type(failure)):
        await gate.claim()
    assert not gate.lock.locked()
    assert await gate.claim() is None
    claim.assert_not_awaited()
    gate.empty_until = 0
    with pytest.raises(type(failure)):
        await gate.claim()
