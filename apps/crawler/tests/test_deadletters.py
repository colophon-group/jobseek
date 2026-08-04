from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

import src.deadletters as deadletters
import src.redis_queue as redis_queue


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(
        decode_responses=True,
        protocol=2,
        socket_timeout=None,
        socket_connect_timeout=None,
    )
    monkeypatch.setattr(redis_queue, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_queue, "_CLAIM_SHA", None)
    monkeypatch.setattr(redis_queue, "_ENQUEUE_SHA", None)
    monkeypatch.setattr(redis_queue, "_RESCHEDULE_SHA", None)
    return fake


def _board_row(
    board_id: str,
    *,
    enabled: bool = True,
    status: str = "active",
    domain: str = "jobs.example.com",
    browser: bool = False,
) -> dict:
    return {
        "board_id": board_id,
        "board_slug": "example-careers",
        "board_url": "https://jobs.example.com/careers",
        "crawler_type": "dom",
        "board_status": status,
        "is_enabled": enabled,
        "throttle_key": domain,
        "monitor_needs_browser": browser,
    }


def _config(*, domain: str = "jobs.example.com", browser: bool = False) -> dict[str, str]:
    return {
        "domain": domain,
        "board_url": "https://jobs.example.com/careers",
        "crawler_type": "dom",
        "monitor_needs_browser": "1" if browser else "0",
    }


async def _seed_deadletter(fake_redis, board_id: str, *, domain: str, wtype: str) -> str:
    member = f"monitor|{domain}|{board_id}"
    await fake_redis.zadd(f"deadletter:{wtype}", {member: time.time()})
    return f"{wtype}:{member}"


async def test_active_poison_stays_inspectable_until_explicit_retry(fake_redis):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="jobs.example.com",
        wtype="simple",
    )
    await fake_redis.hset(f"board:{board_id}", mapping=_config())
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id)]

    inspected = await deadletters.resolve_deadletters(db, action="inspect")
    assert inspected["entries"][0]["lifecycle"] == "actionable"
    assert inspected["entries"][0]["reason"] == "active"
    assert await fake_redis.zcard("deadletter:simple") == 1

    dry_run = await deadletters.resolve_deadletters(
        db,
        action="retry",
        selected_refs=[ref],
    )
    assert dry_run["outcomes"] == [{"ref": ref, "outcome": "would_retry"}]
    assert await fake_redis.zcard("deadletter:simple") == 1

    applied = await deadletters.resolve_deadletters(
        db,
        action="retry",
        selected_refs=[ref],
        apply=True,
    )
    assert applied["outcomes"][0]["outcome"] == "retried"
    assert applied["outcomes"][0]["schedule"] == "enqueued"
    assert await fake_redis.zcard("deadletter:simple") == 0
    assert (
        await fake_redis.zscore(
            "monitors_simple:jobs.example.com",
            board_id,
        )
        is not None
    )


async def test_retry_does_not_duplicate_an_existing_first_time_schedule(fake_redis):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="jobs.example.com",
        wtype="simple",
    )
    await fake_redis.hset(f"board:{board_id}", mapping=_config())
    await fake_redis.zadd("ft_monitors_simple:jobs.example.com", {board_id: time.time()})
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id)]

    result = await deadletters.resolve_deadletters(
        db,
        action="retry",
        selected_refs=[ref],
        apply=True,
    )

    assert result["outcomes"][0]["schedule"] == "already_scheduled"
    assert await fake_redis.zcard("ft_monitors_simple:jobs.example.com") == 1
    assert await fake_redis.zcard("monitors_simple:jobs.example.com") == 0


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([], "removed"),
        (["disabled"], "disabled"),
    ],
)
async def test_prune_requires_authoritative_retirement(fake_redis, rows, reason):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="jobs.example.com",
        wtype="simple",
    )
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id, enabled=False, status="disabled")] if rows else []

    inspected = await deadletters.resolve_deadletters(db, action="inspect")
    assert inspected["entries"][0]["lifecycle"] == "retired"
    assert inspected["entries"][0]["reason"] == reason

    await deadletters.resolve_deadletters(
        db,
        action="prune",
        selected_refs=[ref],
        apply=True,
    )
    assert await fake_redis.zcard("deadletter:simple") == 0


async def test_active_entry_cannot_be_pruned(fake_redis):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="jobs.example.com",
        wtype="simple",
    )
    await fake_redis.hset(f"board:{board_id}", mapping=_config())
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id)]

    with pytest.raises(deadletters.DeadletterResolutionError, match="prune blocked"):
        await deadletters.resolve_deadletters(
            db,
            action="prune",
            selected_refs=[ref],
            apply=True,
        )
    assert await fake_redis.zcard("deadletter:simple") == 1


async def test_superseded_route_is_pruned_only_after_current_schedule_exists(fake_redis):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="old.example.com",
        wtype="simple",
    )
    await fake_redis.hset(
        f"board:{board_id}",
        mapping=_config(domain="new.example.com", browser=True),
    )
    await fake_redis.zadd("monitors_simple:old.example.com", {board_id: time.time()})
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id, domain="new.example.com", browser=True)]

    inspected = await deadletters.resolve_deadletters(db, action="inspect")
    assert inspected["entries"][0]["lifecycle"] == "superseded"
    assert inspected["entries"][0]["reason"] == "monitor_route_changed"

    result = await deadletters.resolve_deadletters(
        db,
        action="prune",
        selected_refs=[ref],
        apply=True,
    )
    assert result["outcomes"][0]["schedule"] == "enqueued"
    assert await fake_redis.zcard("deadletter:simple") == 0
    assert await fake_redis.zcard("monitors_simple:old.example.com") == 0
    assert (
        await fake_redis.zscore(
            "monitors_browser:new.example.com",
            board_id,
        )
        is not None
    )


async def test_active_missing_config_requires_sync_and_preserves_evidence(fake_redis):
    board_id = str(uuid.uuid4())
    ref = await _seed_deadletter(
        fake_redis,
        board_id,
        domain="jobs.example.com",
        wtype="simple",
    )
    db = AsyncMock()
    db.fetch.return_value = [_board_row(board_id)]

    inspected = await deadletters.resolve_deadletters(db, action="inspect")
    assert inspected["entries"][0]["reason"] == "active_config_missing"
    assert inspected["entries"][0]["resolution"] == "sync_then_retry"

    with pytest.raises(deadletters.DeadletterResolutionError, match="retry blocked"):
        await deadletters.resolve_deadletters(
            db,
            action="retry",
            selected_refs=[ref],
            apply=True,
        )
    assert await fake_redis.zcard("deadletter:simple") == 1


async def test_non_monitor_and_malformed_entries_are_unresolved(fake_redis):
    await fake_redis.zadd(
        "deadletter:simple",
        {
            "scrape|example.com|posting|with|pipes": time.time(),
            "broken": time.time(),
        },
    )
    db = AsyncMock()

    entries = await deadletters.classify_deadletters(db)

    assert {entry.reason for entry in entries} == {
        "malformed_descriptor",
        "non_monitor_task",
    }
    db.fetch.assert_not_awaited()
    assert deadletters.lifecycle_counts(entries)["simple"]["unresolved"] == 2


async def test_inspect_rejects_apply_flag(fake_redis):
    db = AsyncMock()

    with pytest.raises(deadletters.DeadletterResolutionError, match="always read-only"):
        await deadletters.resolve_deadletters(db, action="inspect", apply=True)
