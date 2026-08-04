from __future__ import annotations

from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

import src.redis_capacity as capacity
import src.redis_queue as redis_queue


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    monkeypatch.setattr(capacity, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_queue, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_queue, "_CLAIM_SHA", None)
    monkeypatch.setattr(redis_queue, "_ENQUEUE_SHA", None)
    monkeypatch.setattr(redis_queue, "_RESCHEDULE_SHA", None)
    monkeypatch.setattr(redis_queue, "_COMPLETE_SHA", None)
    monkeypatch.setattr(redis_queue, "_HEARTBEAT_SHA", None)
    monkeypatch.setattr(redis_queue, "_REAP_SHA", None)
    return fake


def test_key_families_are_bounded_and_material_names_are_classified() -> None:
    assert capacity.classify_key("scrape:posting") == "scrape_config"
    assert capacity.classify_key("ft_scrapes_browser:lever") == "scrape_queue_first"
    assert capacity.classify_key("scrapes_simple:jobs.example") == "scrape_queue_recurring"
    assert capacity.classify_key("provider_open:workday-303") == "provider_circuit"
    assert capacity.classify_key("new_namespace:value") == "other"
    assert len(capacity.POLICY_BY_NAME) == len(capacity.FAMILY_POLICIES)


async def test_orphan_prune_is_dry_run_first_bounded_and_reachability_safe(fake_redis) -> None:
    await redis_queue.enqueue_scrape(
        "jobs.example.com",
        "reachable",
        0,
        {"source_url": "https://jobs.example.com/1", "board_id": "board-1"},
        first_time=True,
    )
    await fake_redis.hset(
        "scrape:orphan",
        mapping={"domain": "jobs.example.com", "source_url": "https://jobs.example.com/2"},
    )
    await fake_redis.hset("scrape:malformed", mapping={"source_url": "missing-domain"})

    dry_run = await capacity.prune_orphan_scrape_configs(
        max_scanned=100,
        max_delete=1,
        apply=False,
        redis=fake_redis,
    )
    assert dry_run["dry_run"] is True
    assert dry_run["would_delete"] == 1
    assert dry_run["reachable"] == 1
    assert dry_run["missing_domain"] == 1
    assert await fake_redis.exists("scrape:orphan") == 1

    applied = await capacity.prune_orphan_scrape_configs(
        max_scanned=100,
        max_delete=1,
        apply=True,
        redis=fake_redis,
    )
    assert applied["deleted"] == 1
    assert await fake_redis.exists("scrape:orphan") == 0
    assert await fake_redis.exists("scrape:reachable") == 1
    assert await fake_redis.exists("scrape:malformed") == 1


async def test_inventory_reports_reachable_and_orphan_scrape_configs(
    fake_redis, monkeypatch
) -> None:
    # fakeredis does not implement MEMORY USAGE; family counts/state do not
    # depend on byte sampling.
    monkeypatch.setattr(capacity, "SAMPLE_SIZE", 0)

    async def info(section):
        return {
            "memory": {
                "used_memory": 1024,
                "used_memory_rss": 2048,
                "maxmemory": 1024 * 1024,
                "maxmemory_policy": "noeviction",
            },
            "persistence": {"rdb_last_bgsave_status": "ok", "aof_enabled": 0},
            "stats": {"evicted_keys": 0, "total_error_replies": 0},
        }[section]

    monkeypatch.setattr(fake_redis, "info", info)
    await redis_queue.enqueue_scrape(
        "jobs.example.com",
        "reachable",
        0,
        {"source_url": "https://jobs.example.com/1", "board_id": "board-1"},
        first_time=True,
    )
    await fake_redis.hset(
        "scrape:orphan",
        mapping={"domain": "jobs.example.com", "source_url": "https://jobs.example.com/2"},
    )
    snapshot = await capacity.inventory(fake_redis)

    assert snapshot["scrape_config_state"] == {"reachable": 1, "orphan": 1}
    scrape_family = next(
        family for family in snapshot["families"] if family["name"] == "scrape_config"
    )
    assert scrape_family["keys"] == 2
    assert scrape_family["budget_bytes"] == 384 * capacity.MIB
    rendered = capacity.format_prometheus(snapshot)
    assert 'jobseek_redis_scrape_config_state_keys{state="orphan"} 1' in rendered
    assert 'jobseek_redis_key_family_budget_bytes{family="scrape_config"}' in rendered


async def test_rebuild_is_resumable_dry_run_and_uses_durable_schedule(monkeypatch) -> None:
    rows = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "source_url": "https://jobs.example.com/1",
            "board_id": "10000000-0000-0000-0000-000000000001",
            "description_r2_hash": None,
            "next_scrape_at": datetime(2026, 8, 5, tzinfo=UTC),
            "scraper_needs_browser": True,
        }
    ]

    class Pool:
        async def fetch(self, _query, after_id, limit):
            assert after_id is None
            assert limit == 10
            return rows

    captured = []

    async def enqueue(schedules):
        captured.extend(schedules)
        return [True]

    monkeypatch.setattr(capacity, "enqueue_scrapes", enqueue)
    dry_run = await capacity.rebuild_scrape_schedules(Pool(), limit=10, apply=False)
    assert dry_run["selected"] == 1
    assert dry_run["newly_enqueued"] == 0
    assert captured == []

    applied = await capacity.rebuild_scrape_schedules(Pool(), limit=10, apply=True)
    assert applied["newly_enqueued"] == 1
    assert applied["complete"] is True
    assert applied["next_after_id"] == rows[0]["id"]
    assert captured[0].first_time is True
    assert captured[0].next_scrape_at == 0
    assert captured[0].browser is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_scanned": 0}, "max_scanned"),
        ({"max_delete": capacity.MAX_PRUNE_DELETE + 1}, "max_delete"),
        ({"cursor": -1}, "cursor"),
    ],
)
async def test_prune_rejects_unbounded_arguments(fake_redis, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        await capacity.prune_orphan_scrape_configs(redis=fake_redis, **kwargs)
