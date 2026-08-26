"""Redis and deploy contracts for the Umantis identity cutover."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from redis.exceptions import ResponseError

from src.cli import parse_args
from src.umantis_identity_cutover import (
    _park_monitor_batch,
    _repair_batch,
    repair_umantis_identity_cutover,
)


def _row(posting_id: str, board_id: str, source_url: str) -> dict[str, str]:
    return {"posting_id": posting_id, "board_id": board_id, "source_url": source_url}


async def test_redis_cutover_atomically_canonicalizes_existing_hashes() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    canonical = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description"
    rows = [_row("one", "board", canonical), _row("two", "board", canonical)]
    await redis.hset("scrape:one", mapping={"board_id": "board", "source_url": canonical + "/2"})
    await redis.hset("scrape:two", mapping={"board_id": "board", "source_url": canonical})

    assert await _repair_batch(redis, rows) == 1
    assert await redis.hget("scrape:one", "source_url") == canonical
    assert await redis.hget("scrape:two", "source_url") == canonical
    assert await _repair_batch(redis, rows) == 0


async def test_redis_cutover_rejects_a_mismatch_before_changing_any_hash() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    canonical = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description"
    rows = [_row("one", "board", canonical), _row("two", "board", canonical)]
    locale_url = canonical + "/2"
    await redis.hset("scrape:one", mapping={"board_id": "board", "source_url": locale_url})
    await redis.hset(
        "scrape:two",
        mapping={"board_id": "different-board", "source_url": canonical + "/3"},
    )

    with pytest.raises(ResponseError, match="board mismatch"):
        await _repair_batch(redis, rows)

    assert await redis.hget("scrape:one", "source_url") == locale_url
    assert await redis.hget("scrape:two", "source_url") == canonical + "/3"


async def test_cutover_validates_postgres_before_repairing_and_verifies_afterward(
    monkeypatch,
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    canonical = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description"
    rows = [_row("one", "board", canonical)]
    await redis.hset("scrape:one", mapping={"board_id": "board", "source_url": canonical + "/2"})
    connection = AsyncMock()
    connection.fetch.return_value = rows
    monkeypatch.setattr("src.umantis_identity_cutover._migration_sql", lambda: "VALIDATE")
    monkeypatch.setattr("src.umantis_identity_cutover._posting_query", lambda: "FETCH")

    summary = await repair_umantis_identity_cutover(connection, redis)

    connection.execute.assert_awaited_once_with("VALIDATE")
    connection.fetch.assert_awaited_once_with("FETCH")
    assert summary == {
        "postings": 1,
        "redis_hashes_changed": 1,
        "monitor_entries_parked": 0,
    }
    assert await redis.hget("scrape:one", "source_url") == canonical


def test_umantis_cutover_command_has_no_unbounded_arguments(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-umantis-identity-cutover"])
    assert vars(parse_args()) == {
        "command": "repair-umantis-identity-cutover",
        "park_monitors": False,
    }


async def test_rollback_parks_monitor_claims_but_preserves_scrape_dependencies() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    domain = "recruitingapp-2882.umantis.com"
    board_id = "board"
    member = f"monitor|{domain}|{board_id}"
    await redis.hset(f"board:{board_id}", mapping={"domain": domain, "scraper_type": "dom"})
    await redis.zadd(f"monitors_simple:{domain}", {board_id: 1})
    await redis.zadd("inflight:browser", {member: 2})
    await redis.hset("inflight_strikes:browser", member, "1")
    await redis.zadd("deadletter:browser", {member: 3})
    await redis.zadd(f"scrapes_simple:{domain}", {"posting": 4})
    await redis.zadd("ready:simple:1", {domain: 1})

    parked = await _park_monitor_batch(
        redis,
        [{"board_id": board_id, "throttle_key": domain}],
    )

    assert parked == 2
    assert await redis.zscore(f"monitors_simple:{domain}", board_id) is None
    assert await redis.zscore("inflight:browser", member) is None
    assert await redis.hget("inflight_strikes:browser", member) is None
    assert await redis.zscore("deadletter:browser", member) is None
    assert await redis.exists(f"board:{board_id}") == 1
    assert await redis.zscore(f"scrapes_simple:{domain}", "posting") == 4
    assert await redis.zscore("ready:simple:2", domain) == 4
